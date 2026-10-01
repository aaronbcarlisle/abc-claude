#!/usr/bin/env python3
"""
Bash guard hook (PreToolUse, Bash and PowerShell tools).

Enforces global rules from CLAUDE.md that prose alone didn't hold:

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
     `git commit`/`git merge` `-m`/`-F`/`--trailer` (long forms and short
     clusters like `-am` too) and a
     `gh pr|issue|release create|new|edit|comment|review|merge` `-b`/`--body`-style
     option (`-R`/`--repo` anywhere), and a `gh api`
     `-f`/`-F`/`--field`/`--raw-field`/`--input`, whose text carries a
     Co-Authored-By trailer naming Claude or Anthropic, a Claude-Session
     trailer, a "Generated with/by Claude Code" line or a claude.ai/code
     session link. The text can come from a heredoc (`-m "$(cat <<'EOF' ...)"`,
     `-F - <<'EOF'`), a PowerShell here-string or `` `n `` string, or a
     variable set earlier in the command. An `-F`/`--body-file`/`--notes-file`
     file is read relative to the command's modeled cwd. Piped stdin, `$(cat
     file)`, `$'...'` and an unreadable file are allowed.
  4. No plain force-push. Blocks `git push` with `--force`, `-f` (also in a
     cluster like `-uf`) or a `+refspec`; `--force-with-lease` is allowed, so a
     stack can still be restacked.
  5. No merge over red CI. Blocks `gh pr merge` (unless `--auto`, which GitHub
     holds for required checks) and a non-GET `gh api` call to `pulls/N/merge`
     or `merge-async` while `gh pr checks` reports a failing or pending check.
     A PR with no checks at all, or no `gh` on PATH, is allowed.

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
import shutil
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
HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)\\?([A-Za-z_][\w.-]*)\1([^\n]*)\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
HERESTRING = re.compile(r"@(['\"])[ \t]*\r?\n(.*?)\r?\n\1@", re.S)
# Stands in for a heredoc body or here-string while the command is tokenized.
TEXT_MARKER = re.compile("\0(\\d+)\0")
PS_ESCAPES = {"n": "\n", "r": "\r", "t": "\t"}  # in a PowerShell "..." string
ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
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
FORCE_MESSAGE = (
    "PUSH BLOCKED by bash_guard hook: plain force-push. Use `git push "
    "--force-with-lease --force-if-includes`, which refuses to overwrite commits "
    "you haven't fetched.\n"
)
MERGE_MESSAGE = (
    "MERGE BLOCKED by bash_guard hook: PR checks are failing or still pending. "
    "Wait for them to finish green, then retry. `gh pr checks` output:\n\n{output}\n"
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
    # `[:=]`: `git commit --trailer` also takes the `key=value` form.
    re.compile(r"(?im)^[ \t]*co-authored-by[:=][^\n]*\b(claude|anthropic)\b"),
    re.compile(r"(?im)^[ \t]*co-authored-by[:=][^\n]*noreply@anthropic\.com"),
    re.compile(r"(?im)^[ \t]*claude-session[:=]"),
    re.compile(r"(?i)generated (with|by) \[?claude code"),
    re.compile(r"(?i)claude\.ai/code/session_"),
]
# git commit/merge options that carry message text: {name: "value" | "file"}.
GIT_MESSAGE_OPTS = {"-m": "value", "--message": "value", "-F": "file", "--file": "file",
                    "--trailer": "value"}
# Short options that take a value, per subcommand: in a cluster (`-am msg`), the
# rest of the word after one of these is its value, so splitting stops there.
# (`-s` is --signoff for commit but --strategy for merge.)
GIT_SHORT_WITH_VALUE = {"commit": set("mFCctSu"), "merge": set("mFsXS")}
GH_BODY_OPTS = {"-b": "value", "--body": "value", "-F": "file", "--body-file": "file"}
GH_SUBJECT_OPTS = {"-t": "value", "--subject": "value"}
GH_NOTES_OPTS = {"-n": "value", "--notes": "value", "-F": "file", "--notes-file": "file"}
# `new` is gh's alias for `create`.
GH_PR_ACTIONS = {"create", "new", "edit", "comment", "review", "merge"}
GH_ISSUE_ACTIONS = {"create", "new", "edit", "comment"}
GH_RELEASE_ACTIONS = {"create", "new", "edit"}
# gh options that may come before or between the command words, with their value.
GH_GLOBAL_WITH_ARG = {"-R", "--repo"}
GH_API_FIELD_FLAGS = ("-f", "-F", "--field", "--raw-field")
# `gh pr merge` options that take their value as the NEXT token.
MERGE_OPT_WITH_ARG = {"-t", "--subject", "-b", "--body", "-F", "--body-file",
                      "-A", "--author-email", "--match-head-commit", "-R", "--repo"}
MERGE_ENDPOINT = re.compile(r"(?:^|/)repos/([^/]+/[^/]+)/pulls/(\d+)/merge(?:-async)?/?$"
                            r"|(?:^|/)pulls/(\d+)/merge(?:-async)?/?$")


def git(args, cwd, opts=()):
    return subprocess.run(["git", "-C", cwd] + list(opts) + args, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=10)


def tokens(cmd, powershell=False):
    """Shell tokens of `cmd`: words without their quotes, and operators.

    `&`, `|`, `<` and `>` group into one operator (`&&`, `>>`, `2>&`); `;`,
    `(`, `)` and newlines stand alone. A heredoc becomes a `<<` operator and a
    word holding its body (in place, inside a `"$(cat <<EOF ...)"` word), a
    PowerShell here-string a word holding its text, and `#` comments are dropped.
    """
    texts = []

    def stash(text):
        texts.append(text)
        return "'\0%d\0'" % (len(texts) - 1)

    if powershell:
        cmd = HERESTRING.sub(lambda m: stash(m.group(2)), cmd)
    # The body gets newlines around it so its first line starts a line.
    cmd = HEREDOC.sub(lambda m: "<<" + stash("\n%s\n" % m.group(4)) + m.group(3), cmd)
    esc = "`" if powershell else "\\"
    toks, word, quoted, i, n = [], "", False, 0, len(cmd)

    def add(word, quoted):
        if word or quoted:
            word = TEXT_MARKER.sub(lambda m: texts[int(m.group(1))], word)
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
                ch = cmd[i]
                if c == '"' and ch == esc and i + 1 < n and (powershell or cmd[i + 1] in '$`"\\\n'):
                    i += 1
                    ch = PS_ESCAPES.get(cmd[i], cmd[i]) if powershell else cmd[i]
                word += ch
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
        if seg[0] in COMMAND_PREFIXES or ASSIGNMENT.match(seg[0]):
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


def expand_variables(seg, variables, powershell):
    """`seg` with each `$name` / `${name}` word set earlier replaced by its value.
    Records an assignment: `$x = value` in PowerShell (any case), and in bash a
    command made only of `x=value` words."""
    key = str.lower if powershell else str
    if powershell and len(seg) == 3 and seg[0].startswith("$") and seg[1] == "=":
        variables[key(seg[0])] = variables.get(key(seg[2]), seg[2])
        return seg
    seg = [variables.get(key(w), w) for w in seg]
    if not powershell and seg and all(ASSIGNMENT.match(w) for w in seg):
        for w in seg:
            name, _, value = w.partition("=")
            variables["$" + name] = variables["${%s}" % name] = variables.get(value, value)
    return seg


def commands(toks, cwd, powershell=False):
    """(directory, tokens) per simple command, after the directory changes before it.

    `cd` / `Set-Location` move only into a directory that exists (a failed `cd`
    leaves the shell where it was), `pushd` / `popd` keep a stack, and a bash
    subshell's `cd`s end with it. PowerShell parentheses aren't subshells. A
    variable set earlier in the command is replaced by its value.
    """
    stack, subshells, seg, variables = [], [], [], {}
    for t in toks + [";"]:
        if not is_separator(t):
            seg.append(t)
            continue
        seg = strip_prefixes(expand_variables(seg, variables, powershell))
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


def push_forced(args):
    """True if this `git push` forces without a lease: `--force`, `-f` (also in a
    short cluster like `-uf`) or a `+refspec`. `--force-with-lease` is allowed."""
    positional, k = [], 0
    while k < len(args):
        a = args[k]
        if a in PUSH_OPT_WITH_ARG or is_redirect(a):
            k += 2
            continue
        if a == "--force" or (a[:1] == "-" and a[:2] != "--" and "f" in a[1:]):
            return True
        if not a.startswith("-"):
            positional.append(a)
        k += 1
    return any(r.startswith("+") for r in positional[1:])


def merge_target(toks):
    """(PR selector, repo or None) when `toks` merges a PR without `--auto`, else None.
    Covers `gh pr merge` and the REST `pulls/N/merge` and `merge-async` endpoints."""
    if os.path.basename(toks[0]).lower() not in ("gh", "gh.exe") or len(toks) < 3:
        return None
    if [t.lower() for t in toks[1:3]] == ["pr", "merge"]:
        args, selector, repo, k = toks[3:], "", None, 0
        if "--auto" in args:
            return None  # `--auto` only arms auto-merge; GitHub waits for required checks
        while k < len(args):
            if args[k] in MERGE_OPT_WITH_ARG or is_redirect(args[k]):
                if args[k] in GH_GLOBAL_WITH_ARG and k + 1 < len(args):
                    repo = args[k + 1]
                k += 2
                continue
            if not args[k].startswith("-") and not selector:
                selector = args[k]
            k += 1
        return selector, repo
    if toks[1].lower() == "api":
        method, has_fields, endpoint, k = None, False, None, 2
        while k < len(toks):
            t = toks[k]
            if t in ("-X", "--method") and k + 1 < len(toks):
                method, k = toks[k + 1].upper(), k + 2
                continue
            if t.startswith("--method="):
                method = t.split("=", 1)[1].upper()
            elif t in GH_API_FIELD_FLAGS + ("--input",) or is_redirect(t):
                has_fields = has_fields or not is_redirect(t)
                k += 2
                continue
            elif not t.startswith("-") and endpoint is None:
                endpoint = t
            k += 1
        if (method or ("POST" if has_fields else "GET")) == "GET" or not endpoint:
            return None
        m = MERGE_ENDPOINT.search(endpoint)
        if m:
            repo = m.group(1)
            if repo and "{" in repo:
                repo = None  # `{owner}/{repo}` placeholders mean the current repo
            return (m.group(2) or m.group(3)), repo
    return None


def red_checks(cwd, selector, repo):
    """`gh pr checks` output when the PR's checks are failing or pending, else None.
    A repo that runs no CI on the PR counts as green."""
    exe = shutil.which("gh")
    if not exe:
        return None
    cmd = [exe, "pr", "checks"] + ([selector] if selector else []) + (["-R", repo] if repo else [])
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0 or "no checks reported" in out:
        return None
    return out[-3000:]


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


def read_file_text(cwd, path, args=()):
    """`path`'s text, resolved from `cwd`. For `-` (stdin), the heredoc (`<<`) or
    here-string (`<<<`) in `args`. None when neither is known, or the file is
    unreadable."""
    if path == "-":
        for i, a in enumerate(args[:-1]):
            if a in ("<<", "<<<"):
                return args[i + 1]
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
    `file` value is read (`-` from a heredoc in `args`); if unreadable it yields nothing."""
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
            text = read_file_text(cwd, val, args)
            if text is not None:
                yield text
        else:
            yield val


def json_strings(text):
    """Every string in `text` when it is JSON, where a trailer's newlines are `\\n`."""
    try:
        stack = [json.loads(text)]
    except ValueError:
        return
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            yield v
        elif isinstance(v, (dict, list)):
            stack.extend(v.values() if isinstance(v, dict) else v)


def gh_api_texts(cwd, args):
    """Field values from `gh api` `-f`/`-F`/`--field`/`--raw-field key=value` and
    `--input <file>` (and the strings in it, when JSON); a `@file` value is read
    as a file, and `-`/`@-` (stdin) from a heredoc in `args`."""
    i = 0
    while i < len(args):
        a = args[i]
        kv = None
        if (a == "--input" and i + 1 < len(args)) or a.startswith("--input="):
            path = args[i + 1] if a == "--input" else a.split("=", 1)[1]
            i += 2 if a == "--input" else 1
            text = read_file_text(cwd, path, args)
            if text is not None:
                yield text
                yield from json_strings(text)
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
        if val.startswith("@"):
            text = read_file_text(cwd, val[1:], args)
            if text is not None:
                yield text
        else:
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


def split_git_clusters(args, with_value):
    """`args` with short-option clusters split so `-am msg` reads as `-a -m msg`
    and `-sam"msg"` as `-s -a -m"msg"`. A cluster ends at the first letter in
    `with_value`; `-Cmain` stays `-C` with value `main`."""
    out = []
    for a in args:
        if len(a) > 2 and a[0] == "-" and a[1] != "-" and a[1].isalpha() and a[1] not in with_value:
            k = 1
            while k < len(a) and a[k].isalpha() and a[k] not in with_value:
                out.append("-" + a[k])
                k += 1
            if k < len(a):
                out.append("-" + a[k:])
            continue
        out.append(a)
    return out


def gh_word(args):
    """(the first command word in `args`, lowercased, and `args` without it), or
    ("", args). Skips options, and the value of `-R`/`--repo`, which gh takes
    before and between its command words (`gh -R o/r pr create`)."""
    i = 0
    while i < len(args):
        if not args[i].startswith("-"):
            return args[i].lower(), args[:i] + args[i + 1:]
        i += 2 if args[i] in GH_GLOBAL_WITH_ARG else 1
    return "", args


def gh_attribution_hit(cwd, seg):
    """(where, matched line) if a `gh` call in `seg` carries AI attribution, else None."""
    group, args = gh_word(seg[1:])
    if group == "api":
        texts, where = gh_api_texts(cwd, args), "gh api field value"
    else:
        action, args = gh_word(args)
        if group == "pr" and action in GH_PR_ACTIONS:
            spec = dict(GH_BODY_OPTS)
            if action == "merge":
                spec.update(GH_SUBJECT_OPTS)
            texts, where = scan_options(cwd, args, spec), "PR body"
        elif group == "issue" and action in GH_ISSUE_ACTIONS:
            texts, where = scan_options(cwd, args, GH_BODY_OPTS), "issue body"
        elif group == "release" and action in GH_RELEASE_ACTIONS:
            texts, where = scan_options(cwd, args, GH_NOTES_OPTS), "release notes"
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
                args = split_git_clusters(args, GIT_SHORT_WITH_VALUE[sub])
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
                    if push_forced(call[2]):
                        sys.stderr.write(FORCE_MESSAGE)
                        return 2
            if "merge" in cmd:
                target = merge_target(seg)
                if target:
                    output = red_checks(cwd, *target)
                    if output:
                        sys.stderr.write(MERGE_MESSAGE.format(output=output))
                        return 2
    except Exception:
        return 0  # fail open
    return 0


if __name__ == "__main__":
    sys.exit(main())
