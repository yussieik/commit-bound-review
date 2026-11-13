#!/usr/bin/env python3
"""PreToolUse guard for an agent's Bash calls: no push to main and no deploy
unless the commit was reviewed.

Reads the tool-call JSON from stdin. Exit 2 blocks the call and sends stderr
back to the agent as the reason; exit 0 allows it.

The review is one file, `.claude/.review-passed`, holding one commit hash.
A command passes when that hash is HEAD. One more commit and it no longer is.

A command is guarded when its text
- runs `git ... push ... main` (`main`, `+main`, `HEAD:main`, `origin/main`), or
- runs a `git ... push` that switches git's hooks off (`--no-verify`,
  `core.hooksPath`), whatever it pushes: that push would skip the exact
  check in githooks/pre-push, so it gets this one, or
- names a deploy workflow and says `workflow ... run` or `dispatches`
  anywhere (gh, gh api, curl, xargs, a line piped into bash).

The check reads the text (line continuations, backslashes and quotes
dropped), not a parse of the shell line: every round of parsing special
cases (prefixes, wrappers, subshells, eval) left composed forms through.
The cost is that a line which only quotes such a command also needs a
valid marker.

Still past it: text built at run time (`$var`, `echo main | xargs ...`,
brace expansion), a quoted `;` or `|` between git and push, and a bare
`git push` or `--all` on main. For pushes, githooks/pre-push catches those:
git runs it with the real hash, whatever the command looked like. This hook
is the early stop, and the only gate on a deploy command.

For a deploy it checks the checkout the command is run from. It does not
know which commit the workflow will build (`--ref another-branch`).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import NoReturn

# Edit these two for your repository.
BRANCH = "main"
DEPLOY_NAME = re.compile(r"deploy", re.IGNORECASE)

MARKER = os.path.join(".claude", ".review-passed")
DEPLOY_VERB = re.compile(r"\bworkflow\b[^\n;&|]*\brun\b|dispatches", re.IGNORECASE)
# `GIT` runs git on a case-insensitive disk; branch names are exact.
PUSH = re.compile(
    rf"\b(?i:git)\b[^\n;&|]*\bpush\b[^\n;&|]*[\s:/+(`]{re.escape(BRANCH)}(?=$|[\s;&|)`<>])"
)
# A push that skips githooks/pre-push. `-n` is --dry-run for push, not this.
PUSH_UNCHECKED = re.compile(
    r"\bgit\b[^\n;&|]*\bpush\b[^\n;&|]*--no-verify\b|hookspath[^\n;&|]*(?<![\w-])push\b",
    re.IGNORECASE,
)
# Backslashes and quotes (`$'..'` and `$".."` too), which the shell drops
# from a word before it runs it: `"deploy".yml`, `pu\sh`.
_QUOTING = re.compile(r"""\$?['"]|\\""")

# Directories the line may push from: `cd`/`pushd` targets (after options
# like -P), `git -C`, and the git-dir/work-tree flags and variables.
_TARGET = r"""(?:"([^"]+)"|'([^']+)'|([^\s;&|'"`()]+))"""
NAMED_DIR = re.compile(
    rf"""(?:(?<![\w-])(?:cd|pushd)(?:\s+-\w+)*\s+|\s-C\s*|--git-dir[=\s]\s*"""
    rf"""|--work-tree[=\s]\s*|GIT_DIR=|GIT_WORK_TREE=){_TARGET}"""
)


def _joined(cmd: str) -> str:
    """The line with backslash-newline continuations removed, as bash does."""
    return cmd.replace("\\\n", "")


def is_guarded(cmd: str) -> bool:
    text = _QUOTING.sub("", _joined(cmd))
    return bool(
        (DEPLOY_NAME.search(text) and DEPLOY_VERB.search(text))
        or PUSH.search(text)
        or PUSH_UNCHECKED.search(text)
    )


def named_dirs(cmd: str) -> list[str]:
    """Every directory the line names. Over-reading only blocks more, so
    skip what is never a checkout: `cd -`, `cd --`, a `grep -C3` count."""
    out: list[str] = []
    for match in NAMED_DIR.finditer(_joined(cmd)):
        name = next(g for g in match.groups() if g)
        if name not in ("-", "--") and not name.isdigit():
            out.append(name)
    return out


def _git(cwd: str, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def review_dirs(data: dict[str, object], cmd: str) -> list[str]:
    """Checkouts whose marker and HEAD must match: all that the line may push.

    A session in a git worktree pushes that worktree's HEAD and holds its own
    marker, while CLAUDE_PROJECT_DIR still points at the main checkout; the
    payload's cwd is the session's directory. Which checkout a shell line
    pushes is not worth working out exactly, so this fails closed: the
    session's checkout and every directory the line names must each hold a
    valid marker. A wrong guess blocks; it never lets an unreviewed push
    through.
    """
    cwd = data.get("cwd")
    base = (
        cwd
        if isinstance(cwd, str) and cwd
        else os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    )
    dirs: list[str] = []
    for d in [base, *named_dirs(cmd)]:
        path = os.path.normpath(os.path.join(base, os.path.expanduser(d)))
        if os.path.basename(path) == ".git":
            path = os.path.dirname(path)
        top = (
            _git(path, "rev-parse", "--show-toplevel") if os.path.isdir(path) else None
        )
        resolved = top or path
        if resolved not in dirs:
            dirs.append(resolved)
    return dirs


def block(message: str) -> NoReturn:
    print(f"[review-guard] Blocked: {message}", file=sys.stderr)
    sys.exit(2)


def check_marker(cwd: str) -> None:
    try:
        with open(os.path.join(cwd, MARKER), encoding="utf-8") as f:
            reviewed = f.read().strip()
    except OSError:
        block(f"no readable {MARKER} in {cwd}. Review this commit first.")
    head = _git(cwd, "rev-parse", "HEAD")
    if head is None:
        block(f"cannot resolve HEAD in {cwd}.")
    if reviewed != head:
        block(
            f"the review is for {reviewed[:8]}, but HEAD is {head[:8]}. "
            "A commit was made after the review: review again."
        )


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    tool_input = data.get("tool_input")
    cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(cmd, str) or not is_guarded(cmd):
        sys.exit(0)
    for cwd in review_dirs(data, cmd):
        check_marker(cwd)
    sys.exit(0)


if __name__ == "__main__":
    main()
