"""Fake `claude` CLI for the installer tests.

The harness puts a `claude` sh script and a `claude.cmd` first on PATH; both run
this file. It appends each call's arguments to FAKE_CLAUDE_LOG (one line per
call, joined by spaces) and answers the plugin subcommands the installers use
from the JSON state in FAKE_CLAUDE_STATE:

    {"plugins": ["name@marketplace", ...], "marketplaces": ["name", ...], "fail": [...]}

`fail` lists plugin ids and marketplace sources whose install/add fails.
Output formats copy Claude Code 2.1.283 (`claude plugin list --json` etc.).
"""

import json
import os
import sys


def load_state():
    with open(os.environ["FAKE_CLAUDE_STATE"], encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(os.environ["FAKE_CLAUDE_STATE"], "w", encoding="utf-8") as f:
        json.dump(state, f)


def mark_enabled(plugin_id):
    """Mimic the real CLI, which writes enabledPlugins[id] = true to user settings on install."""
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    path = os.path.join(config, "settings.json") if config else ""
    if not path or not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        settings = json.load(f)
    settings.setdefault("enabledPlugins", {})[plugin_id] = True
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")


def emit(text, stream=None):
    (stream or sys.stdout).write(text + "\n")


def main(argv):
    with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8", newline="\n") as f:
        f.write(" ".join(argv) + "\n")
    state = load_state()
    plugins, markets, fail = state["plugins"], state["marketplaces"], state.get("fail", [])

    if argv == ["plugin", "list", "--json"]:
        emit(json.dumps([{
            "id": p, "version": "1.0.0", "scope": "user", "enabled": True,
            "installPath": "/fake/plugins/cache/" + p, "installedAt": "2026-01-01T00:00:00.000Z",
            "lastUpdated": "2026-01-01T00:00:00.000Z",
        } for p in plugins], indent=2))
        return 0
    if argv == ["plugin", "marketplace", "list", "--json"]:
        emit(json.dumps([{
            "name": m, "source": "github", "repo": "example/" + m,
            "installLocation": "/fake/plugins/marketplaces/" + m,
        } for m in markets], indent=2))
        return 0
    if argv[:3] == ["plugin", "marketplace", "add"] and len(argv) == 4:
        source = argv[3]
        if source in fail:
            emit("Failed to add marketplace: " + source, sys.stderr)
            return 1
        name = source.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        if name in markets:
            emit("Marketplace '%s' already on disk - declared in user settings" % name)
        else:
            markets.append(name)
            save_state(state)
            emit("Successfully added marketplace: %s (declared in user settings)" % name)
        return 0
    if argv[:2] == ["plugin", "install"] and len(argv) == 3:
        pid = argv[2]
        if pid in fail:
            emit('Plugin "%s" not found in any marketplace' % pid, sys.stderr)
            return 1
        if pid in plugins:
            emit('Plugin "%s" is already installed (scope: user)' % pid)
            return 0
        plugins.append(pid)
        save_state(state)
        mark_enabled(pid)
        emit("Successfully installed plugin: %s (scope: user)" % pid)
        return 0
    if argv[:2] == ["plugin", "uninstall"] and len(argv) == 3:
        pid = argv[2]
        if pid not in plugins:
            emit('Plugin "%s" is not installed' % pid, sys.stderr)
            return 1
        plugins.remove(pid)
        save_state(state)
        emit("Successfully uninstalled plugin: %s (scope: user)" % pid)
        return 0
    emit("fake claude: unsupported arguments: " + " ".join(argv), sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
