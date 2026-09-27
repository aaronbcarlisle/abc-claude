#!/usr/bin/env python3
"""
Live Claude Code sessions, from ~/.claude/sessions/<pid>.json.

Claude Code writes one file per running session holding its `pid` and `cwd`. A
session is live while its process runs. A checkout is live when a live
session's cwd is inside it and not inside a more specific checkout (a worktree
nested in it). Unreadable files are skipped. A PID reused by another process
still counts as live, which errs toward leaving a checkout alone.

Limitation: a session file records the directory the session was launched in,
not any directory it later `cd`'d into. A session that started elsewhere and
then moved into a checkout (for example by `cd`ing into a worktree) is
invisible to this guard.

    python live_sessions.py PATH [PATH ...]

Exit 0 when no live session is inside any PATH; 1 (listing pid<TAB>cwd) when
one is; 2 on a usage error. CLAUDE_SESSIONS_DIR overrides the directory;
otherwise CLAUDE_CONFIG_DIR/sessions is used when set, then ~/.claude/sessions.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import json
import os
import sys

WINDOWS = os.name == "nt"
WINDOWS_PID_MAX = 2 ** 32 - 1  # PIDs are DWORDs
POSIX_PID_MAX = 2 ** 31 - 1    # pid_t is a signed 32-bit int
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5


def sessions_dir():
    override = os.environ.get("CLAUDE_SESSIONS_DIR")
    if override:
        return override
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        return os.path.join(config_dir, "sessions")
    return os.path.join(os.path.expanduser("~"), ".claude", "sessions")


def _windows_pid_alive(pid):
    """OpenProcess + GetExitCodeProcess. Never os.kill: on Windows it terminates."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED  # exists, not ours
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def pid_alive(pid):
    """True while process `pid` runs. Never signals it on Windows, where os.kill kills.
    The PID bound is per platform: a DWORD on Windows, a signed pid_t on POSIX."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if WINDOWS:
        return pid <= WINDOWS_PID_MAX and _windows_pid_alive(pid)
    if pid > POSIX_PID_MAX:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False
    return True


def norm(path):
    return os.path.normcase(os.path.realpath(path))


def inside(path, root):
    path, root = norm(path), norm(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def live_sessions(directory=None):
    """[{'pid', 'cwd', 'file'}] for every session file whose process runs."""
    directory = directory or sessions_dir()
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    found = []
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        pid, cwd = data.get("pid"), data.get("cwd")
        if isinstance(cwd, str) and cwd and pid_alive(pid):
            found.append({"pid": pid, "cwd": cwd, "file": name})
    return found


def owner(cwd, checkouts):
    """The most specific checkout in `checkouts` that contains `cwd`, or None."""
    best = None
    for checkout in checkouts:
        if inside(cwd, checkout) and (best is None or len(norm(checkout)) > len(norm(best))):
            best = checkout
    return best


def live_checkouts(checkouts, sessions=None):
    """{checkout: [session, ...]} for each checkout a live session is in."""
    sessions = live_sessions() if sessions is None else sessions
    found = {}
    for session in sessions:
        checkout = owner(session["cwd"], checkouts)
        if checkout is not None:
            found.setdefault(checkout, []).append(session)
    return found


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not argv:
        print("usage: live_sessions.py PATH [PATH ...]", file=sys.stderr)
        return 2
    hits = [s for s in live_sessions() if any(inside(s["cwd"], p) for p in argv)]
    for session in hits:
        print("%s\t%s" % (session["pid"], session["cwd"]))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
