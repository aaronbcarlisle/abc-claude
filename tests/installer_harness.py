"""Harness for the installer scenario tests (test_installer.py).

Each Sandbox is a temp directory holding everything one scenario needs, so no
test touches the network, the real ~/.claude or the user's git config:

    root/seed/          working repo the fixture remote is built from (branch master)
    root/remote.git     bare repo used as ABC_CLAUDE_REPO
    root/home/          HOME and USERPROFILE; root/home/.claude is CLAUDE_CONFIG_DIR
    root/downloads/     install.sh and install.ps1 copied out of any git repo
                        (the "download and inspect" route)
    root/bin/           first on PATH; holds the fake claude when enabled
    root/gitconfig      GIT_CONFIG_GLOBAL, with a test identity

Interpreters: "sh" is Git Bash's bash.exe on Windows and `sh` elsewhere;
"powershell" (Windows PowerShell 5.1) and "pwsh" run install.ps1 on Windows only.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
INTERPRETERS = ("sh", "powershell", "pwsh")
FIXTURE_IDENT = ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com")

FIXTURE_SETTINGS = {
    "model": "opus",
    "enabledPlugins": {
        "alpha@claude-plugins-official": True,
        "beta@extra-market": False,
    },
    "extraKnownMarketplaces": {
        "extra-market": {"source": {"source": "github", "repo": "example/extra-market"}},
    },
}

# Files committed to the fixture remote, besides the real ones in COPIED_FILES.
FIXTURE_FILES = {
    "CLAUDE.md": "# Fixture global rules\n",
    "status_line.py": "print('fixture statusline')\n",
    "rules/dev-tooling.md": "# Fixture tooling rules\n",
    "skills/handoff/SKILL.md": "# Fixture skill\n",
    "settings.json": json.dumps(FIXTURE_SETTINGS, indent=2) + "\n",
}
# Real files from this checkout, so the fixture exercises the real ignore rules,
# template and installers.
COPIED_FILES = (".gitignore", ".gitattributes", "install.sh", "install.ps1", "templates/local.md")


def git(*args, cwd=None, check=True):
    """Run git, return stdout (UTF-8)."""
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise AssertionError("git %s failed (%d): %s" % (" ".join(args), proc.returncode, proc.stderr))
    return proc.stdout


def git_bash():
    """Git for Windows' bin/bash.exe, found from `git --exec-path`, or None."""
    try:
        exec_path = subprocess.run(["git", "--exec-path"], capture_output=True, encoding="utf-8",
                                   check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    bash = Path(exec_path).parents[2] / "bin" / "bash.exe"  # <root>/mingw64/libexec/git-core
    return str(bash) if bash.is_file() else None


def interpreter_command(name):
    """argv prefix that runs an installer under `name`, or None when unavailable."""
    if name == "sh":
        if IS_WINDOWS:
            bash = git_bash()
            return [bash] if bash else None
        sh = shutil.which("sh")
        return [sh] if sh else None
    if not IS_WINDOWS:
        return None  # install.ps1 is the Windows installer
    exe = shutil.which("powershell" if name == "powershell" else "pwsh")
    return [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"] if exe else None


def translate_flag(arg, interp):
    """--yes -> -Yes for install.ps1; other arguments pass through."""
    if interp != "sh" and arg.startswith("--"):
        return "-" + arg[2:].capitalize()
    return arg


def clean_path():
    """PATH without any directory holding a real claude, with this Python first."""
    names = ("claude", "claude.exe", "claude.cmd")
    keep = [d for d in os.environ.get("PATH", "").split(os.pathsep)
            if d and not any(os.path.isfile(os.path.join(d, n)) for n in names)]
    return os.pathsep.join([os.path.dirname(sys.executable)] + keep)


def make_writable_and_remove(path):
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                os.chmod(os.path.join(dirpath, name), stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


class Result:
    def __init__(self, proc):
        self.returncode = proc.returncode
        self.stdout = proc.stdout or ""
        self.stderr = proc.stderr or ""
        self.output = ANSI.sub("", self.stdout + self.stderr)

    def __repr__(self):
        return "exit %d\n%s" % (self.returncode, self.output)


class Sandbox:
    def __init__(self):
        self.root = Path(os.path.realpath(tempfile.mkdtemp(prefix="abc-claude-test-")))
        self.seed = self.root / "seed"
        self.remote = self.root / "remote.git"
        self.home = self.root / "home"
        self.target = self.home / ".claude"
        self.downloads = self.root / "downloads"
        self.bin = self.root / "bin"
        self.gitconfig = self.root / "gitconfig"
        self.no_managed = self.root / "no-managed-settings.json"
        self.claude_log = self.root / "claude-calls.log"
        self.claude_state = self.root / "claude-state.json"
        for d in (self.home, self.downloads, self.bin):
            d.mkdir(parents=True)
        self.set_global_identity("Test User", "test@example.com")
        for name in ("install.sh", "install.ps1"):
            if (REPO_ROOT / name).is_file():
                shutil.copyfile(REPO_ROOT / name, self.downloads / name)

    def cleanup(self):
        make_writable_and_remove(self.root)

    # --- fixture remote -----------------------------------------------------
    def make_remote(self, files=None):
        """Build seed (master) from FIXTURE_FILES + COPIED_FILES + `files`, then bare-clone it to remote.git."""
        content = dict(FIXTURE_FILES)
        for rel in COPIED_FILES:
            src = REPO_ROOT / rel
            content[rel] = src.read_bytes() if src.is_file() else b"# not in this checkout yet\n"
        content.update(files or {})
        self.seed.mkdir()
        git("init", "-q", "-b", "master", cwd=self.seed)
        git("config", "core.autocrlf", "false", cwd=self.seed)
        self.write_files(self.seed, content)
        git("add", "-A", cwd=self.seed)
        git(*FIXTURE_IDENT, "commit", "-q", "-m", "fixture", cwd=self.seed)
        git("clone", "-q", "--bare", str(self.seed), str(self.remote))
        git("remote", "add", "origin", str(self.remote), cwd=self.seed)

    def push_change(self, files, message="fixture change"):
        """Commit `files` in seed and push them to remote.git."""
        self.write_files(self.seed, files)
        git("add", "-A", cwd=self.seed)
        git(*FIXTURE_IDENT, "commit", "-q", "-m", message, cwd=self.seed)
        git("push", "-q", "origin", "master", cwd=self.seed)

    @staticmethod
    def write_files(base, files):
        for rel, data in files.items():
            path = Path(base) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)

    def set_global_identity(self, name, email):
        lines = ["[user]"]
        if name:
            lines.append("\tname = " + name)
        if email:
            lines.append("\temail = " + email)
        self.gitconfig.write_text("\n".join(lines) + "\n" if len(lines) > 1 else "", encoding="utf-8")

    # --- fake claude --------------------------------------------------------
    def fake_claude(self, plugins=(), marketplaces=("claude-plugins-official",), fail=()):
        """Put a logging fake `claude` (sh script + claude.cmd) in bin/."""
        self.claude_state.write_text(json.dumps({"plugins": list(plugins), "marketplaces": list(marketplaces),
                                                 "fail": list(fail)}), encoding="utf-8")
        py = sys.executable.replace("\\", "/")
        script = str(TESTS_DIR / "fake_claude.py").replace("\\", "/")
        sh_stub = self.bin / "claude"
        sh_stub.write_bytes(('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (py, script)).encode("utf-8"))
        sh_stub.chmod(0o755)
        (self.bin / "claude.cmd").write_bytes(
            ('@echo off\r\n"%s" "%s" %%*\r\nexit /b %%ERRORLEVEL%%\r\n' % (sys.executable, script)).encode("utf-8"))

    def claude_calls(self):
        if not self.claude_log.is_file():
            return []
        return self.claude_log.read_text(encoding="utf-8").splitlines()

    def claude_plugins(self):
        return json.loads(self.claude_state.read_text(encoding="utf-8"))["plugins"]

    # --- running the installer ----------------------------------------------
    def env(self, **overrides):
        env = dict(os.environ)
        for key in ("ABC_CLAUDE_REPO", "CLAUDE_CONFIG_DIR", "ABC_CLAUDE_MANAGED_PATHS", "GIT_DIR",
                    "GIT_WORK_TREE", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"):
            env.pop(key, None)
        env.update({
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "CLAUDE_CONFIG_DIR": str(self.target),
            "ABC_CLAUDE_REPO": str(self.remote),
            "ABC_CLAUDE_MANAGED_PATHS": str(self.no_managed),
            "GIT_CONFIG_GLOBAL": str(self.gitconfig),
            "GIT_TERMINAL_PROMPT": "0",
            "FAKE_CLAUDE_LOG": str(self.claude_log),
            "FAKE_CLAUDE_STATE": str(self.claude_state),
            "PATH": str(self.bin) + os.pathsep + clean_path(),
        })
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = str(value)
        return env

    def run(self, interp, *args, script_dir=None, stdin="", env=None, timeout=300):
        """Run the installer from `script_dir` (default downloads/) under `interp`."""
        prefix = interpreter_command(interp)
        if prefix is None:
            raise AssertionError(interp + " is not available")
        base = Path(script_dir) if script_dir else self.downloads
        if interp == "sh":
            script = str(base / "install.sh").replace("\\", "/")
        else:
            script = str(base / "install.ps1")
        argv = prefix + [script] + [translate_flag(a, interp) for a in args]
        proc = subprocess.run(argv, input=stdin, capture_output=True, encoding="utf-8", errors="replace",
                              env=env if env is not None else self.env(), cwd=str(self.root), timeout=timeout)
        return Result(proc)


# --- assertions helpers -------------------------------------------------------
def snapshot(path):
    """{relative path: bytes} for every file under `path`, including .git."""
    out = {}
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            full = Path(dirpath) / name
            out[full.relative_to(path).as_posix()] = full.read_bytes()
    return out


def backups(path):
    """Relative paths of *.bak.<14 digits> files under `path`, outside .git."""
    pattern = re.compile(r"\.bak\.\d{14}$")
    return sorted(rel for rel in snapshot(path) if not rel.startswith(".git/") and pattern.search(rel))


def tracked_files(path):
    return [line for line in git("-C", str(path), "ls-files").splitlines() if line]
