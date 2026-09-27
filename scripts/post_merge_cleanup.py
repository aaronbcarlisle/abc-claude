#!/usr/bin/env python3
"""
Post-merge cleanup: delete merged local branches and their worktrees,
fast-forward the default branch, optionally run the tests, and log the result
to the handoff.

    post_merge_cleanup.py [--repo PATH] [--here] [--test CMD] [--handoff PATH] [--dry-run] [--json]

A local branch is deleted only when nothing unpushed is lost. That means one of:
- its tip is an ancestor of origin/<default>;
- a merged PR exists for it whose head contains the tip.

A checked-out branch whose tip is an ancestor but that was never pushed under
its own name (its upstream isn't refs/heads/<branch>, e.g. it tracks the
default or has none) is kept unless a merged PR contains its tip: it's new
work that hasn't started. Checkouts with a live Claude Code session are
skipped; --here exempts the caller's own checkout. A failed switch or delete
keeps that branch and the run goes on. Never commits and never pushes.
--dry-run changes nothing (not even a fetch).

Exit: 0 done, 1 tests failed, 2 refused or error.

Tests: python -m unittest discover -s tests (from the repo root)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live_sessions  # noqa: E402

GH_UNAVAILABLE = "gh unavailable: merged-PR check skipped; squash-merged branches may be kept"


class Refused(Exception):
    pass


def run(argv, cwd, check=True):
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError("%s failed (%d): %s" % (" ".join(argv), proc.returncode, proc.stderr.strip()))
    return proc


def git(cwd, *args):
    return run(["git", *args], cwd).stdout.strip()


def git_ok(cwd, *args):
    return run(["git", *args], cwd, check=False).returncode == 0


def gh_json(cwd, *args, notes=None):
    """Parsed JSON from `gh args`, or None when gh is missing or fails. On None, adds
    GH_UNAVAILABLE to `notes` once."""
    exe = shutil.which("gh")
    data = None
    if exe:
        proc = run([exe, *args], cwd, check=False)
        if proc.returncode == 0:
            try:
                data = json.loads(proc.stdout)
            except ValueError:
                data = None
    if data is None and notes is not None and GH_UNAVAILABLE not in notes:
        notes.append(GH_UNAVAILABLE)
    return data


def default_branch(repo, notes=None):
    data = gh_json(repo, "repo", "view", "--json", "defaultBranchRef", notes=notes)
    name = ((data or {}).get("defaultBranchRef") or {}).get("name") if isinstance(data, dict) else None
    if name:
        return name
    ref = run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo, check=False).stdout.strip()
    if ref.startswith("origin/"):
        return ref[len("origin/"):]
    for name in ("main", "master"):
        if git_ok(repo, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/" + name):
            return name
    raise Refused("can't determine the default branch")


def worktrees(repo):
    """[{'path', 'branch', 'locked', 'prunable', 'bare'}]; the first entry is the main checkout."""
    found, current = [], None
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            current = {"path": line[len("worktree "):], "branch": None,
                       "locked": False, "prunable": False, "bare": False}
            found.append(current)
        elif current is None:
            continue
        elif line.startswith("branch refs/heads/"):
            current["branch"] = line[len("branch refs/heads/"):]
        elif line.startswith("locked"):
            current["locked"] = True
        elif line.startswith("prunable"):
            current["prunable"] = True
        elif line == "bare":
            current["bare"] = True
    return found


def dirty(path, untracked):
    args = ["status", "--porcelain"] + ([] if untracked else ["--untracked-files=no"])
    return bool(git(path, *args))


def merged_pr(repo, branch, tip, fetch, notes=None):
    """('#N', None) when merged PR #N's head contains `tip`; (None, '#N') when merged PRs exist
    but none contains it; (None, None) when there is no merged PR."""
    prs = gh_json(repo, "pr", "list", "--state", "merged", "--head", branch,
                  "--json", "number,headRefOid", "--limit", "20", notes=notes)
    seen = None
    for pr in prs if isinstance(prs, list) else []:
        oid, number = pr.get("headRefOid"), pr.get("number")
        if not oid or number is None:
            continue
        seen = "#%s" % number
        if fetch and not git_ok(repo, "cat-file", "-e", oid + "^{commit}"):
            run(["git", "fetch", "--quiet", "origin", "refs/pull/%s/head" % number], repo, check=False)
        if git_ok(repo, "merge-base", "--is-ancestor", tip, oid):
            return "#%s" % number, None
    return None, seen


def cleanup(args):
    report = {"repo": None, "default": None, "sha": None, "deleted": [], "kept": [],
              "removed_worktrees": [], "pulled": False, "tests": "skipped", "test_output": "",
              "notes": [], "dry_run": bool(args.dry_run)}
    start = os.path.abspath(args.repo or os.getcwd())
    top = run(["git", "rev-parse", "--show-toplevel"], start, check=False) if os.path.isdir(start) else None
    if top is None or top.returncode != 0:
        raise Refused("not a git repository: " + start)
    if not args.dry_run:
        git(top.stdout.strip(), "worktree", "prune")
    trees = worktrees(top.stdout.strip())
    main_tree = trees[0]
    if main_tree["bare"]:
        raise Refused("bare repository: " + main_tree["path"])
    repo = main_tree["path"]
    report["repo"] = repo
    paths = [t["path"] for t in trees]
    here = live_sessions.owner(os.getcwd(), paths) if args.here else None
    live = {c: s for c, s in live_sessions.live_checkouts(paths).items() if c != here}
    if repo in live:
        raise Refused("a live Claude Code session is in the main checkout (pid %s)"
                      % ", ".join(str(s["pid"]) for s in live[repo]))
    main_dirty = dirty(repo, untracked=False)
    if main_dirty and not args.here:
        raise Refused("the main checkout has uncommitted changes")

    notes = report["notes"]
    default = default_branch(repo, notes)
    report["default"] = default
    if not args.dry_run:
        git(repo, "fetch", "--prune", "--quiet", "origin")
    target = "refs/remotes/origin/" + default
    checked_out = {t["branch"]: t for t in trees if t["branch"]}

    for ref in git(repo, "for-each-ref", "--format=%(refname)", "refs/heads/").splitlines():
        branch = ref[len("refs/heads/"):]
        if branch == default:
            continue

        def keep(reason):
            report["kept"].append({"branch": branch, "reason": reason})

        tip = git(repo, "rev-parse", ref)
        tree = checked_out.get(branch)
        ancestor = git_ok(repo, "merge-base", "--is-ancestor", tip, target)
        upstream = run(["git", "config", "--get", "branch.%s.merge" % branch], repo, check=False).stdout.strip()
        never_pushed = bool(ancestor and tree) and upstream != ref
        why = "merged into " + default if ancestor and not never_pushed else None
        if why is None:
            pr, other = merged_pr(repo, branch, tip, fetch=not args.dry_run, notes=notes)
            if pr:
                why = "PR %s merged" % pr
            elif never_pushed:
                keep("checked out, no commits and never pushed")
                continue
            elif other:
                keep("has commits not in merged PR %s" % other)
                continue
            else:
                keep("not merged")
                continue

        if tree:
            path = tree["path"]
            if path == repo:
                if main_dirty:
                    keep("checked out in the main checkout, which has uncommitted changes")
                    continue
                if not args.dry_run:
                    switched = run(["git", "switch", "--quiet", default], repo, check=False)
                    if switched.returncode != 0:
                        keep("switch to %s failed: %s" % (default, switched.stderr.strip()))
                        continue
            elif path == here:
                keep("checked out in this session's worktree: remove it after this session ends")
                continue
            elif path in live:
                keep("worktree %s has a live session" % path)
                continue
            elif tree["locked"]:
                keep("worktree %s is locked" % path)
                continue
            elif not tree["prunable"]:
                if dirty(path, untracked=True):
                    keep("worktree %s has uncommitted or untracked files" % path)
                    continue
                if not args.dry_run:
                    removed = run(["git", "worktree", "remove", path], repo, check=False)
                    if removed.returncode != 0:
                        keep("worktree remove failed: %s" % removed.stderr.strip())
                        continue
                report["removed_worktrees"].append(path)
        if not args.dry_run:
            deleted = run(["git", "branch", "-D", branch], repo, check=False)
            if deleted.returncode != 0:
                reason = "branch -D failed: %s" % deleted.stderr.strip()
                if tree and tree["path"] in report["removed_worktrees"]:
                    reason += " (its worktree %s was already removed)" % tree["path"]
                keep(reason)
                continue
        report["deleted"].append({"branch": branch, "reason": why})

    finish(repo, default, args, report)
    return report


def short_sha(repo, default):
    """Short SHA of the local default branch, or of HEAD when that branch is missing."""
    proc = run(["git", "rev-parse", "--short", "--verify", "--quiet", "refs/heads/" + default], repo, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else git(repo, "rev-parse", "--short", "HEAD")


def finish(repo, default, args, report):
    """Pull, test and log. Task 4 fills this in."""
    report["sha"] = short_sha(repo, default)


def render(report):
    return json.dumps(report, indent=2)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Delete merged branches and worktrees after a merge.")
    parser.add_argument("--repo", help="any path inside the repo (default: the current directory)")
    parser.add_argument("--here", action="store_true",
                        help="the caller is the session in the checkout holding the current directory")
    parser.add_argument("--test", metavar="CMD", help="test command to run in the main checkout")
    parser.add_argument("--handoff", metavar="PATH", help="handoff file to log to")
    parser.add_argument("--dry-run", action="store_true", help="report what would happen; change nothing")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    if args.repo:
        args.repo = os.path.expanduser(args.repo)
    if args.handoff:
        args.handoff = os.path.expanduser(args.handoff)
    try:
        report = cleanup(args)
        code = 1 if report["tests"] == "fail" else 0
    except Refused as exc:
        report, code = {"refused": str(exc)}, 2
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        report, code = {"error": str(exc)}, 2
    print(json.dumps(report, indent=2) if args.json else render(report))
    return code


if __name__ == "__main__":
    sys.exit(main())
