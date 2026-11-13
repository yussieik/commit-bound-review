#!/usr/bin/env python3
"""PreToolUse guard for an agent's Bash calls: no destructive git command
while the working tree has uncommitted changes.

Reads the tool-call JSON from stdin. Exit 2 blocks the call and sends stderr
back to the agent as the reason; exit 0 allows it.

Guarded: `git checkout` (except `checkout -b`, which creates a branch and
destroys nothing), `git restore`, `git reset --hard`, `git stash drop|clear`
and `git branch -D`. On a clean tree they all pass.

The tree checked is the session's own directory. A line that first moves to
another repository (`cd ../other && git checkout .`) is judged by the wrong
tree; this guard is for the common accident, an agent undoing its own test
change with a checkout, not for a line built to get past it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

_GIT = r"\bgit\b(?:[^|;&\n]*\s)?"
DESTRUCTIVE = re.compile(
    rf"{_GIT}(?:checkout\b(?!\s+-b\b)|restore\b|reset\s+--hard\b"
    rf"|stash\s+(?:drop|clear)\b|branch\b[^|;&\n]*\s-D\b)"
)


def is_dirty(cwd: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    tool_input = data.get("tool_input")
    cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(cmd, str) or not DESTRUCTIVE.search(cmd):
        sys.exit(0)
    cwd = data.get("cwd")
    base = (
        cwd
        if isinstance(cwd, str) and cwd
        else os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    )
    if is_dirty(base):
        print(
            "[dirty-tree-guard] Blocked: destructive git command while the tree "
            "has uncommitted changes. Commit or stash first, or ask the user.",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
