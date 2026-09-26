#!/usr/bin/env python3
"""
Claude Code Status Line

Layout:  Model ver | repo @ branch | 312k/1M | cache 91% 52m | 5h 42% · 7d 71%

  - Model name + version  (accent-colored by model family)
  - Active repo @ git branch  (green=clean, red=dirty/uncommitted edits)
  - Context tokens / window size, colored by absolute tokens, not percentage:
    every turn re-reads the whole context, so cost tracks the token count.
    grey <150k, yellow 150k+ (plan the handoff), orange 250k+ (hand off at the
    next task boundary), red 400k+. Thresholds are the CONTEXT_* constants.
  - Prompt cache  (session hit ratio + minutes until the cached prefix goes cold:
    green while warm, yellow in the last 5 min (last 1 min on a 5m TTL), red
    "cold ~45k" once expired, with the tokens the next request will re-cache).
    Reads `prompt_cache` (Claude Code 2.1.251+); hidden when caching isn't observed.
  - Plan usage limits  (`rate_limits.five_hour` / `seven_day` used_percentage:
    grey <50%, yellow 50%+, orange 75%+, red 90%+ — the LIMIT_* constants).
    Pro/Max only and only after the first API response; each window is hidden
    when absent.

Reads the session JSON from stdin (see https://code.claude.com/docs/en/statusline).
The repo name comes from `workspace.repo.name` — no subprocess — falling back to
the basename of `workspace.current_dir`. Branch + dirty state still need git.
"""

import json
import os
import subprocess
import sys
import time

if sys.version_info < (3, 8):
    print("Python 3.8+ required")
    sys.exit(1)

sys.stdout.reconfigure(encoding="utf-8")

# =============================================================================
# COLOR PALETTE
# =============================================================================
RESET = "\033[0m"
DIM = "\033[90m"                  # Grey  — separators / secondary text
WHITE = "\033[97m"                # Primary text
GREEN = "\033[38;2;34;197;94m"    # Clean branch
RED = "\033[38;2;239;68;68m"      # Dirty branch / critical context
YELLOW = "\033[33m"               # Elevated context
ORANGE = "\033[38;5;208m"         # Near-limit context

# Model accent colors, matched by substring of the model name.
MODEL_COLORS = {
    "opus": "\033[38;2;217;70;239m",    # Purple — premium
    "sonnet": "\033[38;2;59;130;246m",  # Blue   — balanced
    "haiku": "\033[38;2;34;197;94m",    # Green  — quick
    "fable": "\033[38;2;245;158;11m",   # Amber  — fable family
}

# Context-token thresholds for the context section's colors.
CONTEXT_PLAN = 150_000      # yellow: plan where to hand off
CONTEXT_HANDOFF = 250_000   # orange: hand off at the next task boundary
CONTEXT_HIGH = 400_000      # red: every turn is now expensive

# Usage-limit thresholds (percent of the window used) for the limits section.
LIMIT_WARN = 50       # yellow
LIMIT_HIGH = 75       # orange
LIMIT_CRITICAL = 90   # red
LIMIT_WINDOWS = (("five_hour", "5h"), ("seven_day", "7d"))


# =============================================================================
# HELPERS
# =============================================================================

def paint(text, color):
    """Wrap text in an ANSI color + reset."""
    return f"{color}{text}{RESET}"


def git_branch(cwd):
    """Return (branch_name, is_dirty); (None, False) when not a git repo."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=cwd, timeout=2,
        )
        if head.returncode != 0:
            return None, False
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=cwd, timeout=2,
        )
        return head.stdout.strip(), bool(status.stdout.strip())
    except Exception:
        return None, False


def repo_name(workspace, cwd):
    """Active repo name from the JSON, falling back to the directory basename."""
    name = (workspace.get("repo") or {}).get("name")
    if name:
        return name
    directory = workspace.get("current_dir") or cwd
    return os.path.basename(os.path.normpath(directory)) if directory else None


def model_section(model_info):
    """Accent-colored 'Brand ver' from the model dict (or string)."""
    if isinstance(model_info, dict):
        model = model_info.get("display_name") or model_info.get("id") or "?"
    else:
        model = str(model_info) if model_info else "?"

    color = next((c for k, c in MODEL_COLORS.items() if k in model.lower()), WHITE)
    brand, _, ver = model.partition(" ")
    return f"{paint(brand, color)} {paint(ver, DIM)}".rstrip() if ver else paint(brand, color)


def repo_branch_section(workspace, cwd):
    """'repo @ branch*' — repo in white, branch colored by dirty state."""
    name = repo_name(workspace, cwd)
    branch, is_dirty = git_branch(cwd)
    if not name and not branch:
        return None

    parts = []
    if name:
        parts.append(paint(name, WHITE))
    if branch:
        marker = "*" if is_dirty else ""
        parts.append(paint(f"{branch}{marker}", RED if is_dirty else GREEN))
    return paint(" @ ", DIM).join(parts)


def short_tokens(n):
    """1_000_000 -> '1M', 312_400 -> '312k', 900 -> '900'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1_000:
        return f"{round(n / 1_000)}k"
    return str(n)


def context_section(context_window):
    """'312k/1M' — tokens in the context window, colored by absolute size."""
    tokens = context_window.get("total_input_tokens")
    size = context_window.get("context_window_size")
    if not tokens:
        pct = context_window.get("used_percentage")
        return paint(f"{int(pct)}%", DIM) if pct else None

    if tokens < CONTEXT_PLAN:
        color = DIM
    elif tokens < CONTEXT_HANDOFF:
        color = YELLOW
    elif tokens < CONTEXT_HIGH:
        color = ORANGE
    else:
        color = RED
    text = f"{short_tokens(tokens)}/{short_tokens(size)}" if size else short_tokens(tokens)
    return paint(text, color)


def cache_section(prompt_cache):
    """'cache 91% 52m' while warm; 'cache cold ~45k' once the prefix has expired."""
    if not prompt_cache or not prompt_cache.get("caching_observed"):
        return None

    expires_at = prompt_cache.get("expires_at")
    left = int(expires_at - time.time()) if expires_at else 0
    if prompt_cache.get("warm") and left > 0:
        ratio = prompt_cache.get("hit_ratio")
        hit = f"{round(ratio * 100)}%" if ratio is not None else "--"
        minutes = f"{left // 60}m" if left >= 60 else "<1m"
        warn_at = 60 if prompt_cache.get("ttl") == "5m" else 300
        return paint(f"cache {hit} {minutes}", YELLOW if left < warn_at else GREEN)

    rebuild = prompt_cache.get("recache_tokens_if_cold")
    cost = f" ~{round(rebuild / 1000)}k" if rebuild else ""
    return paint(f"cache cold{cost}", RED)


def limits_section(rate_limits):
    """'5h 42% · 7d 71%' — plan usage limits; each window hidden when absent."""
    if not isinstance(rate_limits, dict):
        return None
    parts = []
    for key, label in LIMIT_WINDOWS:
        window = rate_limits.get(key)
        pct = window.get("used_percentage") if isinstance(window, dict) else None
        if isinstance(pct, bool) or not isinstance(pct, (int, float)):
            continue
        if pct < LIMIT_WARN:
            color = DIM
        elif pct < LIMIT_HIGH:
            color = YELLOW
        elif pct < LIMIT_CRITICAL:
            color = ORANGE
        else:
            color = RED
        parts.append(f"{paint(label, DIM)} {paint(f'{round(pct)}%', color)}")
    return paint(" · ", DIM).join(parts) if parts else None


# =============================================================================
# MAIN
# =============================================================================

def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception as e:
        print(f"Error: {e}")
        return

    cwd = data.get("workspace", {}).get("current_dir") or data.get("cwd") or os.getcwd()
    workspace = data.get("workspace") or {}

    sections = [
        model_section(data.get("model", {})),
        repo_branch_section(workspace, cwd),
        context_section(data.get("context_window") or {}),
        cache_section(data.get("prompt_cache")),
        limits_section(data.get("rate_limits")),
    ]
    print(" | ".join(s for s in sections if s))


if __name__ == "__main__":
    main()
