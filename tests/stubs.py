"""Shared test helpers: fake CLIs first on PATH, and an isolated git config.

install_stub writes `name.cmd` on Windows (subprocess and shutil.which find it
through PATHEXT) and an executable sh script elsewhere; both run `script` with
this Python. It mirrors the stub format of installer_harness.fake_claude.
"""

import os
import subprocess
import sys


def install_stub(bin_dir, name, script):
    os.makedirs(bin_dir, exist_ok=True)
    if os.name == "nt":
        path = os.path.join(bin_dir, name + ".cmd")
        body = '@echo off\r\n"%s" "%s" %%*\r\nexit /b %%ERRORLEVEL%%\r\n' % (sys.executable, script)
    else:
        path = os.path.join(bin_dir, name)
        body = '#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, script)
    with open(path, "wb") as f:
        f.write(body.encode("utf-8"))
    os.chmod(path, 0o755)
    return path


def git_env(tmp, **extra):
    """os.environ plus an isolated git identity and config under `tmp`, plus `extra`.

    GIT_CEILING_DIRECTORIES stops repository discovery at `tmp`, so a git call in
    a directory under `tmp` that isn't a repo can never find an enclosing real one.
    """
    config = os.path.join(tmp, "gitconfig")
    with open(config, "w", encoding="utf-8", newline="\n") as f:
        f.write("[user]\n\tname = Test\n\temail = test@example.com\n"
                "[init]\n\tdefaultBranch = main\n[core]\n\tautocrlf = false\n")
    env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    parent = os.path.dirname(os.path.abspath(tmp))
    # Both spellings, in case one is an 8.3 short path (e.g. RUNNER~1 on CI).
    ceilings = list(dict.fromkeys([parent, os.path.dirname(os.path.realpath(tmp))]))
    env.update(GIT_CONFIG_GLOBAL=config, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
               GIT_CEILING_DIRECTORIES=os.pathsep.join(ceilings))
    env.update({key: str(value) for key, value in extra.items()})
    return env


def git(cwd, env, *args):
    proc = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise AssertionError("git %s failed (%d): %s" % (" ".join(args), proc.returncode, proc.stderr))
    return proc.stdout.strip()
