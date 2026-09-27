#!/usr/bin/env python3
"""
Bash guard hook (PreToolUse, Bash and PowerShell tools).

Enforces two global rules from CLAUDE.md that prose alone didn't hold:

  1. Never push to the default branch. Blocks a `git push` whose refspec, bare
     or `HEAD` push (while on it), `--all` or `--mirror` would update the
     remote's default branch (`refs/remotes/<remote>/HEAD`; `main` and `master`
     when that is unknown). Deny rules can't express this: only a trailing `*`
     expands, so `git push * main` never matches.
  2. Write multi-line files with the Write tool. Blocks a heredoc fed to `cat`
     or `tee` that lands in a file, and a PowerShell here-string written with
     Set-Content, Add-Content, Out-File or a redirect. Heredocs that feed a
     program (`python - <<'EOF'`) stay allowed.

Commands are tokenized (quotes, newlines and heredoc bodies handled), so text
in an `echo` or a commit message never trips a gate. Each command is checked
in the directory it really runs in: the hook input's `cwd`, moved by `cd` /
`Set-Location`, then `git -C`.

Exit 0 = allow. Exit 2 = block; stderr goes back to Claude. Fails open on any
error, so a broken guard never blocks work.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import json
import os
import re
import shlex
import subprocess
import sys

FALLBACK_DEFAULTS = {"main", "master"}
# git global options that take their value as the NEXT token (`-C dir`, `-c k=v`).
GIT_GLOBAL_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
# `git push` options that take their value as the NEXT token.
PUSH_OPT_WITH_ARG = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
CD_COMMANDS = {"cd", "chdir", "pushd", "set-location", "sl", "push-location"}
HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_]\w*)\1([^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
HERESTRING = re.compile(r"@(['\"])[ \t]*\r?\n.*?\r?\n\1@", re.S)
PS_FILE_WRITE = re.compile(r"(?i)\b(set-content|add-content|out-file)\b|\[(system\.)?io\.file\]::write|(?<![<>=-])>>?\s*[\w$\"'./\\~]")
SEPARATOR_CHARS = set(";&|()\n")
FILE_REDIRECTS = {">", ">>", ">|", "&>", "&>>", "1>", "1>>"}

PUSH_MESSAGE = (
    "PUSH BLOCKED by bash_guard hook: this push would update the default branch "
    "`{branch}`. Push a feature branch and open a PR instead (global rule in "
    "~/.claude/CLAUDE.md).\n"
)
HEREDOC_MESSAGE = (
    "BLOCKED by bash_guard hook: write multi-line file content with the Write "
    "tool (append with Edit), not a heredoc or here-string (global rule in "
    "~/.claude/CLAUDE.md). Heredocs that feed a program, like `python - <<'EOF'`, "
    "are fine.\n"
)


def git(args, cwd):
    return subprocess.run(["git", "-C", cwd] + args, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=10)


def tokens(cmd):
    """Shell tokens of `cmd`; heredoc bodies become a `<<` `HEREDOC` pair."""
    cmd = HEREDOC.sub(lambda m: "<<HEREDOC" + m.group(3), cmd)
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|()<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes: best effort
        return cmd.split()


def pipelines(cmd):
    """Pipelines of simple commands: lists of token lists."""
    pipeline, seg = [], []
    for t in tokens(cmd) + [";"]:
        # `&&`, `;`, newline end a pipeline; `|` joins one; `>`, `2>&1` are redirects.
        if set(t) <= SEPARATOR_CHARS:
            if seg:
                pipeline.append(seg)
            seg = []
            if t != "|" and pipeline:
                yield pipeline
                pipeline = []
        else:
            seg.append(t)


def resolve(base, path):
    path = os.path.expanduser(path.replace("${HOME}", "~").replace("$HOME", "~"))
    m = re.match(r"^/([a-zA-Z])(?:/|$)(.*)", path)  # Git Bash /d/x -> D:/x
    if m and os.name == "nt":
        path = "%s:/%s" % (m.group(1).upper(), m.group(2))
    return os.path.normpath(os.path.join(base, path))


def strip_env(seg):
    while seg and re.match(r"^[A-Za-z_]\w*=", seg[0]):  # FOO=bar cmd
        seg = seg[1:]
    return seg


def commands(cmd, cwd):
    """(directory, tokens) per simple command, tracking `cd` between them."""
    for pipeline in pipelines(cmd):
        for seg in pipeline:
            seg = strip_env(seg)
            if not seg:
                continue
            if seg[0].lower() in CD_COMMANDS:
                args = [a for a in seg[1:] if not a.startswith("-")]
                if args:
                    cwd = resolve(cwd, args[0])
                continue
            yield cwd, seg


def push_call(cwd, toks):
    """(directory, args after `push`) for a `git ... push ...` command, else None."""
    if os.path.basename(toks[0]).lower() not in ("git", "git.exe"):
        return None
    j = 1
    while j < len(toks) and toks[j].startswith("-"):
        if toks[j] in GIT_GLOBAL_WITH_ARG:
            if toks[j] == "-C" and j + 1 < len(toks):
                cwd = resolve(cwd, toks[j + 1])
            j += 2
        else:
            j += 1  # --no-pager, --git-dir=x, --bare, ...
    if j < len(toks) and toks[j] == "push":
        return cwd, toks[j + 1:]
    return None


def default_branches(cwd, remote):
    r = git(["symbolic-ref", "--quiet", "--short", "refs/remotes/%s/HEAD" % remote], cwd)
    name = r.stdout.strip()
    if r.returncode == 0 and "/" in name:
        return {name.split("/", 1)[1]}
    return set(FALLBACK_DEFAULTS)


def push_target(cwd, args):
    """The default branch this `git push` would update, else None."""
    positional, k = [], 0
    while k < len(args):
        a = args[k]
        if a in PUSH_OPT_WITH_ARG:
            k += 2
            continue
        if not a.startswith("-"):
            positional.append(a)
        k += 1
    remote = positional[0] if positional else "origin"
    defaults = default_branches(cwd, remote)
    protected = defaults | {"refs/heads/" + b for b in defaults}
    if any(a in ("--all", "--mirror", "--branches") for a in args):
        return sorted(defaults)[0]
    refspecs = positional[1:]
    if not refspecs or any(r.lstrip("+") == "HEAD" for r in refspecs):
        current = git(["branch", "--show-current"], cwd).stdout.strip()
        if current in protected:
            return current
    for r in refspecs:
        dest = r.lstrip("+").split(":")[-1]
        if dest in protected:
            return dest.replace("refs/heads/", "")
    return None


def heredoc_file_write(cmd):
    """True if a heredoc fed to `cat` / `tee` ends up in a file."""
    if "<<" not in cmd:
        return False
    for pipeline in pipelines(cmd):
        segs = [strip_env(s) for s in pipeline]
        segs = [s for s in segs if s]
        fed = any(os.path.basename(s[0]).lower() in ("cat", "tee") and "<<" in s for s in segs)
        if not fed:
            continue
        for s in segs:
            if os.path.basename(s[0]).lower() == "tee" and any(not a.startswith("-") and a not in ("<<", "HEREDOC") for a in s[1:]):
                return True
            for i, t in enumerate(s[:-1]):
                if t in FILE_REDIRECTS and not s[i + 1].startswith("&") and s[i + 1] != "/dev/null":
                    return True
    return False


def herestring_file_write(cmd):
    """True if a PowerShell here-string sits in a command that writes a file."""
    if not HERESTRING.search(cmd):
        return False
    # Judge the command around the here-string, not the text inside it.
    return bool(PS_FILE_WRITE.search(HERESTRING.sub("@''@", cmd)))


def main():
    try:
        data = json.load(sys.stdin)
        cmd = (data.get("tool_input") or {}).get("command", "")
        if not cmd:
            return 0
        if heredoc_file_write(cmd) or herestring_file_write(cmd):
            sys.stderr.write(HEREDOC_MESSAGE)
            return 2
        if "push" not in cmd:
            return 0
        for cwd, toks in commands(cmd, data.get("cwd") or os.getcwd()):
            call = push_call(cwd, toks)
            if call:
                branch = push_target(*call)
                if branch:
                    sys.stderr.write(PUSH_MESSAGE.format(branch=branch))
                    return 2
    except Exception:
        return 0  # fail open
    return 0


if __name__ == "__main__":
    sys.exit(main())
