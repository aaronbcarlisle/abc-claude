#!/usr/bin/env python3
"""
Bash guard hook (PreToolUse, Bash and PowerShell tools).

Enforces two global rules from CLAUDE.md that prose alone didn't hold:

  1. Never push to the default branch. Blocks a `git push` that would update
     the remote's default branch (`refs/remotes/<remote>/HEAD`; `main` and
     `master` when that is unknown): an explicit or wildcard refspec, `:`,
     `--all` or `--mirror`, a `HEAD` push while on it, and a push without
     refspecs that `remote.<name>.push` or `push.default` sends there
     (`matching`, or `upstream` tracking the default branch). Deny rules can't
     express this: only a trailing `*` expands, so `git push * main` never
     matches.
  2. Write multi-line files with the Write tool. Blocks a heredoc fed to `cat`
     or `tee` that lands in a file, also through a `( )` or `{ }` group, and a
     PowerShell here-string written with Set-Content, Add-Content, Out-File or
     a redirect. Heredocs that feed a program (`python - <<'EOF'`) stay allowed.
  3. Never add AI attribution to a commit message or PR/issue text. Blocks a
     `git commit`/`git merge` `-m`/`-F`/`--trailer` (and long forms) and a
     `gh pr|issue|release create|edit|comment|review|merge` `-b`/`--body`-style
     option, and a `gh api` `-f`/`-F`/`--field`/`--raw-field`/`--input`, whose
     text carries a Co-Authored-By trailer naming Claude or Anthropic, a
     Claude-Session trailer, a "Generated with/by Claude Code" line or a
     claude.ai/code session link. An `-F`/`--body-file`/`--notes-file` file is
     read relative to the command's modeled cwd; `-F -` (stdin) and an
     unreadable file are allowed.

Commands are tokenized (quotes, escapes, comments and heredoc bodies handled;
PowerShell escapes with a backtick, so its Windows paths keep their
backslashes), so text in an `echo`, a commit message or a quoted argument never
trips a gate. Each command is checked in the directory it really runs in: the
hook input's `cwd`, moved by `cd` / `Set-Location` into a directory that
exists and by `pushd` / `popd`, restored when a bash subshell ends, then
`git -C`.

Exit 0 = allow. Exit 2 = block; stderr goes back to Claude. Fails open on any
error, so a broken guard never blocks work.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import fnmatch
import json
import os
import re
import subprocess
import sys

FALLBACK_DEFAULTS = {"main", "master"}
# git global options that take their value as the NEXT token (`-C dir`, `-c k=v`).
GIT_GLOBAL_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
# Global options that change the repo or config the push sees; lookups reuse them.
GIT_GLOBAL_KEPT = {"-c", "--git-dir", "--work-tree"}
# `git push` options that take their value as the NEXT token.
PUSH_OPT_WITH_ARG = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
CD_COMMANDS = {"cd", "chdir", "set-location", "sl"}
PUSHD_COMMANDS = {"pushd", "push-location"}
POPD_COMMANDS = {"popd", "pop-location"}
# Words that can come before a command without changing it: `if git push ...; then`.
COMMAND_PREFIXES = {"{", "}", "!", "if", "then", "elif", "else", "do", "while", "until",
                    "time", "command", "exec", "nohup", "env"}
HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)\\?([A-Za-z_][\w.-]*)\1([^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
HERESTRING = re.compile(r"@(['\"])[ \t]*\r?\n.*?\r?\n\1@", re.S)
PS_QUOTED = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"`]|`.)*\"", re.S)
# A PowerShell file write: the cmdlets, [IO.File]::Write*, or a redirect of the
# output stream (`>`, `>>`, `1>`, `*>`; not `2>` to `6>`, and not to `$null`).
PS_FILE_WRITE = re.compile(r"(?i)\b(set-content|add-content|out-file)\b|\[(system\.)?io\.file\]::write"
                           r"|(?<![<>=\-2-6])>>?\s*(?!\$null\b)[\w$\"'./\\~]")
OP_CHARS = ";&|()<>\n"
SEPARATORS = {";", "(", ")", "\n"}  # single-character tokens; `&` and `|` runs also separate
PIPES = {"|", "|&"}
FILE_REDIRECTS = {">", ">>", ">|", "&>", "&>>", "1>", "1>>", "1>|"}
# A quoted word made only of these keeps its quotes, so it never reads as syntax.
QUOTE_KEPT = set(OP_CHARS + "{}!")

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
ATTRIBUTION_MESSAGE = (
    "ATTRIBUTION BLOCKED by bash_guard hook: the {where} contains AI attribution "
    "({snippet}). Remove Co-Authored-By trailers naming Claude or Anthropic, "
    "\"Generated with Claude Code\" lines and claude.ai/code session links "
    "(global rule in ~/.claude/CLAUDE.md).\n"
)

# A trailer line, a "Generated with/by Claude Code" line, or a claude.ai session
# link, anywhere case-insensitive. The first two are anchored to a line start
# (`re.MULTILINE`) so prose mentioning Claude mid-sentence never matches.
ATTRIBUTION_PATTERNS = [
    re.compile(r"(?im)^[ \t]*co-authored-by:[^\n]*\b(claude|anthropic)\b"),
    re.compile(r"(?im)^[ \t]*co-authored-by:[^\n]*noreply@anthropic\.com"),
    re.compile(r"(?im)^[ \t]*claude-session:"),
    re.compile(r"(?i)generated (with|by) \[?claude code"),
    re.compile(r"(?i)claude\.ai/code/session_"),
]
# git commit/merge options that carry message text: {name: "value" | "file"}.
GIT_MESSAGE_OPTS = {"-m": "value", "--message": "value", "-F": "file", "--file": "file",
                    "--trailer": "value"}
GH_BODY_OPTS = {"-b": "value", "--body": "value", "-F": "file", "--body-file": "file"}
GH_SUBJECT_OPTS = {"-t": "value", "--subject": "value"}
GH_NOTES_OPTS = {"-n": "value", "--notes": "value", "-F": "file", "--notes-file": "file"}
GH_PR_ACTIONS = {"create", "edit", "comment", "review", "merge"}
GH_ISSUE_ACTIONS = {"create", "edit", "comment"}
GH_RELEASE_ACTIONS = {"create", "edit"}
GH_API_FIELD_FLAGS = ("-f", "-F", "--field", "--raw-field")


def git(args, cwd, opts=()):
    return subprocess.run(["git", "-C", cwd] + list(opts) + args, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=10)


def tokens(cmd, powershell=False):
    """Shell tokens of `cmd`: words without their quotes, and operators.

    `&`, `|`, `<` and `>` group into one operator (`&&`, `>>`, `2>&`); `;`,
    `(`, `)` and newlines stand alone. A heredoc body becomes a `<<` `HEREDOC`
    pair, a PowerShell here-string an empty word, and `#` comments are dropped.
    """
    cmd = HEREDOC.sub(lambda m: "<<HEREDOC" + m.group(3), cmd)
    if powershell:
        cmd = HERESTRING.sub("''", cmd)
    esc = "`" if powershell else "\\"
    toks, word, quoted, i, n = [], "", False, 0, len(cmd)

    def add(word, quoted):
        if word or quoted:
            toks.append("'%s'" % word if quoted and word and set(word) <= QUOTE_KEPT else word)

    while i < n:
        c = cmd[i]
        if c == esc and i + 1 < n:
            if cmd[i + 1] != "\n":  # an escaped newline continues the line
                word += cmd[i + 1]
                quoted = True
            i += 2
        elif c in "'\"":
            quoted, i = True, i + 1
            while i < n and cmd[i] != c:
                if c == '"' and cmd[i] == esc and i + 1 < n and (powershell or cmd[i + 1] in '$`"\\\n'):
                    i += 1
                word += cmd[i]
                i += 1
            i += 1
        elif c == "#" and not word and not quoted:
            i = cmd.find("\n", i)
            i = n if i < 0 else i
        elif c in " \t\r" or c in OP_CHARS:
            fd = word if c in "<>" and word.isdigit() and not quoted else ""  # 2>, 1>>
            if not fd:
                add(word, quoted)
            word, quoted = "", False
            j = i + 1
            if c in "&|<>":
                while j < n and cmd[j] in "&|<>":
                    j += 1
            if c in OP_CHARS:
                toks.append(fd + cmd[i:j])
            i = j
        else:
            word += c
            i += 1
    add(word, quoted)
    return toks


def is_separator(t):
    """True for a token that ends a simple command: `;`, `&&`, `|`, `(`, ..."""
    return t in SEPARATORS or (bool(t) and set(t) <= set("&|"))


def is_redirect(t):
    return t[:1] != "'" and bool(set(t) & set("<>"))


def strip_prefixes(seg):
    """`seg` without leading assignments (`FOO=bar`, `$x =`) and words like `if` or `{`."""
    while seg:
        if seg[0] in COMMAND_PREFIXES or re.match(r"^[A-Za-z_]\w*=", seg[0]):
            seg = seg[1:]
        elif len(seg) > 1 and seg[1] == "=" and seg[0].startswith("$"):
            seg = seg[2:]
        else:
            break
    return seg


def arguments(seg):
    """The words after a command's name, minus options, redirects and their targets."""
    out, i = [], 1
    while i < len(seg):
        if is_redirect(seg[i]):
            i += 2
            continue
        if not seg[i].startswith("-"):
            out.append(seg[i])
        i += 1
    return out


def resolve(base, path):
    """`path` seen from `base`, with ~, $HOME, $VAR, $env:VAR and /d/x drive paths expanded."""
    path = re.sub(r"^\$\{?HOME\}?(?=[/\\]|$)", "~", path)
    path = re.sub(r"(?i)\$\{?env:(\w+)\}?", lambda m: os.environ.get(m.group(1), m.group(0)), path)
    path = os.path.expanduser(os.path.expandvars(path))
    m = re.match(r"^/([a-zA-Z])(?:/|$)(.*)", path)  # Git Bash /d/x -> D:/x
    if m and os.name == "nt":
        path = "%s:/%s" % (m.group(1).upper(), m.group(2))
    return os.path.normpath(os.path.join(base, path))


def commands(toks, cwd, powershell=False):
    """(directory, tokens) per simple command, after the directory changes before it.

    `cd` / `Set-Location` move only into a directory that exists (a failed `cd`
    leaves the shell where it was), `pushd` / `popd` keep a stack, and a bash
    subshell's `cd`s end with it. PowerShell parentheses aren't subshells.
    """
    stack, subshells, seg = [], [], []
    for t in toks + [";"]:
        if not is_separator(t):
            seg.append(t)
            continue
        seg = strip_prefixes(seg)
        name = seg[0].lower() if seg else ""
        if name in POPD_COMMANDS:
            if stack:
                cwd = stack.pop()
        elif name in CD_COMMANDS or name in PUSHD_COMMANDS:
            args = arguments(seg)
            target = resolve(cwd, args[0]) if args else ""
            if target and os.path.isdir(target):
                if name in PUSHD_COMMANDS:
                    stack.append(cwd)
                cwd = target
        elif seg:
            yield cwd, seg
        seg = []
        if t == "(" and not powershell:
            subshells.append((cwd, list(stack)))
        elif t == ")" and subshells and not powershell:
            cwd, stack = subshells.pop()


def push_call(cwd, toks):
    """(directory, kept global options, args after `push`) for `git ... push`, else None."""
    if os.path.basename(toks[0]).lower() not in ("git", "git.exe"):
        return None
    opts, j = [], 1
    while j < len(toks) and toks[j].startswith("-"):
        t = toks[j]
        if t in GIT_GLOBAL_WITH_ARG and j + 1 < len(toks):
            if t == "-C":
                cwd = resolve(cwd, toks[j + 1])
            elif t in GIT_GLOBAL_KEPT:
                opts += [t, toks[j + 1]]
            j += 2
        else:
            if t.startswith(("--git-dir=", "--work-tree=")):
                opts.append(t)
            j += 1  # --no-pager, --bare, ...
    if j < len(toks) and toks[j] == "push":
        return cwd, opts, toks[j + 1:]
    return None


def git_config(cwd, opts):
    """Effective config as {key: [values]}. git lowercases section and variable
    names but keeps a subsection's case (`branch.Feat/X.merge`)."""
    config = {}
    for entry in git(["config", "--list", "-z"], cwd, opts).stdout.split("\0"):
        key, _, value = entry.partition("\n")
        if key:
            config.setdefault(key, []).append(value)
    return config


def default_branches(cwd, opts, remote):
    r = git(["symbolic-ref", "--quiet", "--short", "refs/remotes/%s/HEAD" % remote], cwd, opts)
    name = r.stdout.strip()
    if r.returncode == 0 and "/" in name:
        return {name.split("/", 1)[1]}
    return set(FALLBACK_DEFAULTS)


def refspec_target(spec, current, defaults):
    """The default branch that refspec `spec` would update, else None."""
    spec = spec.lstrip("+")
    if spec.startswith("^"):  # a negative refspec only excludes
        return None
    src, colon, dst = spec.partition(":")
    if not colon:
        dst = src  # `main` updates `main`
    if not dst:  # `:` pushes every matching branch; git rejects `main:`
        return None if src else sorted(defaults)[0]
    if dst in ("HEAD", "@"):
        dst = current
    if dst.startswith("refs/heads/"):
        dst = dst[len("refs/heads/"):]
    for branch in sorted(defaults):
        if fnmatch.fnmatchcase(branch, dst):  # also `refs/heads/*`
            return branch
    return None


def push_target(cwd, opts, args):
    """The default branch this `git push` would update, else None."""
    positional, repo, k = [], None, 0
    while k < len(args):
        a = args[k]
        if a in PUSH_OPT_WITH_ARG or is_redirect(a):  # `2>&` `1` isn't a refspec
            if a == "--repo" and k + 1 < len(args):
                repo = args[k + 1]
            k += 2
            continue
        if a.startswith("--repo="):
            repo = a.split("=", 1)[1]
        elif not a.startswith("-"):
            positional.append(a)
        k += 1
    current = git(["branch", "--show-current"], cwd, opts).stdout.strip()
    config = git_config(cwd, opts)

    def get(key):
        return (config.get(key) or [""])[-1]

    # A repository argument wins over --repo (git-push(1)); with neither, git
    # uses the branch's push remote.
    remote = (positional[0] if positional else repo or get("branch.%s.pushremote" % current)
              or get("remote.pushdefault") or get("branch.%s.remote" % current) or "origin")
    defaults = default_branches(cwd, opts, remote)
    if any(a in ("--all", "--mirror", "--branches") for a in args):
        return sorted(defaults)[0]
    # Without refspecs, remote.<name>.push decides, else push.default.
    refspecs = positional[1:] or config.get("remote.%s.push" % remote, [])
    if not refspecs:
        mode = get("push.default") or "simple"
        if mode == "matching":
            refspecs = [":"]
        elif mode in ("upstream", "tracking"):
            refspecs = ["HEAD:" + (get("branch.%s.merge" % current) or current)]
        elif mode != "nothing":  # simple, current
            refspecs = ["HEAD"]
    for spec in refspecs:
        branch = refspec_target(spec, current, defaults)
        if branch:
            return branch
    return None


def statements(toks):
    """Top-level statements, as lists of simple commands.

    They split on `;`, `&&`, `||`, `&` and newlines outside ( ) and { }
    groups, so a pipeline, or a group and the redirect after it, stays one.
    """
    stmt, seg, depth = [], [], 0
    for t in toks + [";"]:
        if not is_separator(t):
            seg.append(t)
            continue
        while seg and seg[0] in ("{", "}"):
            depth += 1 if seg[0] == "{" else -1
            seg = seg[1:]
        if seg:
            stmt.append(seg)
        seg = []
        depth = max(0, depth + (t == "(") - (t == ")"))
        if depth == 0 and t not in PIPES and t not in ("(", ")"):
            if stmt:
                yield stmt
            stmt = []


def heredoc_file_write(toks):
    """True if a heredoc fed to `cat` / `tee` ends up in a file."""
    if "<<" not in toks:
        return False
    for stmt in statements(toks):
        segs = [s for s in (strip_prefixes(s) for s in stmt) if s]
        if not any(os.path.basename(s[0]).lower() in ("cat", "tee") and "<<" in s for s in segs):
            continue
        for s in segs:
            if os.path.basename(s[0]).lower() == "tee" and arguments(s):
                return True
            for i, t in enumerate(s[:-1]):
                if t in FILE_REDIRECTS and not s[i + 1].startswith("&") and s[i + 1] != "/dev/null":
                    return True
    return False


def herestring_file_write(cmd):
    """True if a PowerShell here-string sits in a command that writes a file."""
    if not HERESTRING.search(cmd):
        return False
    # Judge the command around the here-string, not the text inside it or in quoted arguments.
    return bool(PS_FILE_WRITE.search(PS_QUOTED.sub("''", HERESTRING.sub("@''@", cmd))))


def read_file_text(cwd, path):
    """`path`'s text, resolved from `cwd`; None for `-` (stdin) or an unreadable file."""
    if path == "-":
        return None
    try:
        with open(resolve(cwd, path), "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def attribution_match(text):
    """The attribution line in `text`, trimmed to ~80 chars, else None."""
    for pat in ATTRIBUTION_PATTERNS:
        m = pat.search(text)
        if m:
            start = text.rfind("\n", 0, m.start()) + 1
            end = text.find("\n", m.end())
            line = text[start:len(text) if end < 0 else end].strip()
            return line[:80].rstrip() + "..." if len(line) > 80 else line
    return None


def scan_options(cwd, args, spec):
    """The text carried by options in `args` matching `spec` ({name: "value"|"file"}):
    `-x val`, `-xval` (two-character names only), `--long val`, `--long=val`. A
    `file` value of `-` (stdin) or an unreadable path yields nothing."""
    i = 0
    while i < len(args):
        a = args[i]
        val, step, kind = None, 1, None
        for name, k in spec.items():
            if a == name:
                if i + 1 < len(args):
                    val, step = args[i + 1], 2
                kind = k
                break
            if len(name) == 2 and a.startswith(name) and a != name:
                val, kind = a[len(name):], k
                break
            if name.startswith("--") and a.startswith(name + "="):
                val, kind = a[len(name) + 1:], k
                break
        i += step
        if val is None:
            continue
        if kind == "file":
            if val != "-":
                text = read_file_text(cwd, val)
                if text is not None:
                    yield text
        else:
            yield val


def gh_api_texts(cwd, args):
    """Field values from `gh api` `-f`/`-F`/`--field`/`--raw-field key=value` and
    `--input <file>`; a `@file` value is read as a file, `@-` is stdin (allowed)."""
    i = 0
    while i < len(args):
        a = args[i]
        kv = None
        if a in ("--input",) and i + 1 < len(args):
            text = read_file_text(cwd, args[i + 1])
            if text is not None:
                yield text
            i += 2
            continue
        if a.startswith("--input="):
            text = read_file_text(cwd, a.split("=", 1)[1])
            if text is not None:
                yield text
            i += 1
            continue
        if a in GH_API_FIELD_FLAGS and i + 1 < len(args):
            kv, i = args[i + 1], i + 2
        elif len(a) > 2 and a[:2] in ("-f", "-F"):
            kv, i = a[2:], i + 1
        elif a.startswith(("--field=", "--raw-field=")):
            kv, i = a.split("=", 1)[1], i + 1
        else:
            i += 1
            continue
        _, sep, val = kv.partition("=")
        if not sep:
            continue
        if val.startswith("@") and val != "@-":
            text = read_file_text(cwd, val[1:])
            if text is not None:
                yield text
        elif val != "@-":
            yield val


def git_subcommand(cwd, toks):
    """(directory, subcommand, args after it) for a `git [global opts] <sub> ...`
    call, else None. `-C` moves `cwd`; other global options are skipped."""
    if os.path.basename(toks[0]).lower() not in ("git", "git.exe"):
        return None
    j = 1
    while j < len(toks) and toks[j].startswith("-"):
        t = toks[j]
        if t in GIT_GLOBAL_WITH_ARG and j + 1 < len(toks):
            if t == "-C":
                cwd = resolve(cwd, toks[j + 1])
            j += 2
        else:
            j += 1  # --no-pager, --bare, --git-dir=..., ...
    if j < len(toks):
        return cwd, toks[j].lower(), toks[j + 1:]
    return None


def gh_attribution_hit(cwd, seg):
    """(where, matched line) if a `gh` call in `seg` carries AI attribution, else None."""
    if len(seg) < 2:
        return None
    group = seg[1].lower()
    if group == "api":
        texts, where = gh_api_texts(cwd, seg[2:]), "gh api field value"
    else:
        if len(seg) < 3:
            return None
        action = seg[2].lower()
        if group == "pr" and action in GH_PR_ACTIONS:
            spec = dict(GH_BODY_OPTS)
            if action == "merge":
                spec.update(GH_SUBJECT_OPTS)
            texts, where = scan_options(cwd, seg[3:], spec), "PR body"
        elif group == "issue" and action in GH_ISSUE_ACTIONS:
            texts, where = scan_options(cwd, seg[3:], GH_BODY_OPTS), "issue body"
        elif group == "release" and action in GH_RELEASE_ACTIONS:
            texts, where = scan_options(cwd, seg[3:], GH_NOTES_OPTS), "release notes"
        else:
            return None
    for text in texts:
        line = attribution_match(text)
        if line:
            return where, line
    return None


def attribution_hit(cwd, seg):
    """(where, matched line) if `seg` carries AI attribution in a commit message
    or PR/issue text, else None."""
    name = os.path.basename(seg[0]).lower()
    if name in ("git", "git.exe"):
        call = git_subcommand(cwd, seg)
        if call:
            sub_cwd, sub, args = call
            if sub in ("commit", "merge"):
                for text in scan_options(sub_cwd, args, GIT_MESSAGE_OPTS):
                    line = attribution_match(text)
                    if line:
                        return "commit message", line
        return None
    if name in ("gh", "gh.exe"):
        return gh_attribution_hit(cwd, seg)
    return None


def main():
    try:
        data = json.load(sys.stdin)
        cmd = (data.get("tool_input") or {}).get("command", "")
        if not cmd:
            return 0
        powershell = data.get("tool_name") == "PowerShell"
        toks = tokens(cmd, powershell)
        if heredoc_file_write(toks) or herestring_file_write(cmd):
            sys.stderr.write(HEREDOC_MESSAGE)
            return 2
        cwd0 = data.get("cwd") or os.getcwd()
        for cwd, seg in commands(toks, cwd0, powershell):
            hit = attribution_hit(cwd, seg)
            if hit:
                where, line = hit
                sys.stderr.write(ATTRIBUTION_MESSAGE.format(where=where, snippet=line))
                return 2
            if "push" in cmd:
                call = push_call(cwd, seg)
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
