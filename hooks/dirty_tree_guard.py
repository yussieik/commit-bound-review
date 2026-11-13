#!/usr/bin/env python3
"""PreToolUse guard for an agent's Bash calls: no git command that throws
away uncommitted work.

Reads the tool-call JSON from stdin. Exit 2 blocks the call and sends stderr
back to the agent as the reason; exit 0 allows it.

Refused while the working tree has uncommitted changes:
- `git checkout` (except `-b`, `-B`, `--orphan`, which create a branch)
- `git restore` (except `--staged` alone, which only unstages)
- `git reset --hard`
- `git clean` with `-f` (except a dry run)
- `git switch` with `-f`, `--force` or `--discard-changes`
Refused always: `git stash drop` and `git stash clear`. A stash is
uncommitted work too, and the tree is clean right after `git stash`.

Unlike review_guard.py this one splits the line into words, because it has
to tell `git checkout .` from `git commit -m "fix checkout flow"`. A line
the tokenizer cannot split (a heredoc with an apostrophe in it) is judged by
its text instead: on a dirty tree, any of those subcommands blocks it.

The tree checked is the session's own directory. A line that first moves to
another repository (`cd ../other && git checkout .`) is judged by the wrong
tree; and text built at run time (`$cmd`, xargs) is not seen at all. This
guard is for the common accident, an agent undoing its own test change with
a checkout, not for a line built to get past it.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

# git's own options that take a separate value: `git -C dir checkout .`
VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
SHELLS = {"bash", "sh", "zsh", "dash", "eval"}
# When the line cannot be split into words: any of these subcommands after git.
FALLBACK = re.compile(
    r"\bgit\b[^|;&\n]*\b(?:checkout|restore|reset|clean|switch|stash)\b", re.IGNORECASE
)

ALWAYS, ON_DIRTY_TREE = "always", "on a dirty tree"


def _short(args: list[str], letter: str) -> bool:
    """A short flag, alone or in a cluster: `-f`, `-fd`, `-xfd`."""
    return any(a.startswith("-") and not a.startswith("--") and letter in a for a in args)


def verdict(subcommand: str, args: list[str]) -> str | None:
    """When this git command destroys uncommitted work, or None if never."""
    if subcommand == "checkout":
        creates = {"-b", "-B", "--orphan"}
        return None if creates & set(args) else ON_DIRTY_TREE
    if subcommand == "restore":
        staged = "--staged" in args or _short(args, "S")
        worktree = "--worktree" in args or _short(args, "W")
        return None if staged and not worktree else ON_DIRTY_TREE
    if subcommand == "reset":
        return ON_DIRTY_TREE if "--hard" in args else None
    if subcommand == "clean":
        forced = "--force" in args or _short(args, "f")
        dry = "--dry-run" in args or _short(args, "n")
        return ON_DIRTY_TREE if forced and not dry else None
    if subcommand == "switch":
        forced = {"--force", "--discard-changes"} & set(args) or _short(args, "f")
        return ON_DIRTY_TREE if forced else None
    if subcommand == "stash":
        action = next((a for a in args if not a.startswith("-")), "")
        return ALWAYS if action in ("drop", "clear") else None
    return None


def _segments(cmd: str) -> list[list[str]]:
    """The line as commands, each a list of words. Raises ValueError when the
    quoting does not balance."""
    lexer = shlex.shlex(
        cmd.replace("\\\n", "").replace("`", " "), posix=True, punctuation_chars=True
    )
    lexer.whitespace = " \t\r"  # a newline ends a command, so keep it as a token
    segments: list[list[str]] = [[]]
    for token in lexer:
        if token and all(c in "();<>|&\n" for c in token):
            segments.append([])
        else:
            segments[-1].append(token)
    return segments


def verdicts(cmd: str) -> set[str]:
    """Every way this line destroys uncommitted work."""
    try:
        segments = _segments(cmd)
    except ValueError:
        return {ON_DIRTY_TREE} if FALLBACK.search(cmd) else set()
    found: set[str] = set()
    for words in segments:
        names = [os.path.basename(w).lower() for w in words]
        for at, name in enumerate(names):
            if name != "git":
                continue
            rest = iter(words[at + 1 :])
            for word in rest:
                if word in VALUE_OPTIONS:
                    next(rest, None)
                elif not word.startswith("-"):
                    result = verdict(word, list(rest))
                    if result:
                        found.add(result)
        # `bash -c '...'` and `eval '...'` carry a whole line in one word.
        if SHELLS & set(names):
            for word in words:
                if " " in word or "\n" in word:
                    found |= verdicts(word)
    return found


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
    if not isinstance(cmd, str) or "git" not in cmd.lower():
        sys.exit(0)
    found = verdicts(cmd)
    if not found:
        sys.exit(0)
    cwd = data.get("cwd")
    base = (
        cwd
        if isinstance(cwd, str) and cwd
        else os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    )
    if ALWAYS in found:
        reason = "this line drops a stash, which is uncommitted work"
    elif is_dirty(base):
        reason = "destructive git command while the tree has uncommitted changes"
    else:
        sys.exit(0)
    print(
        f"[dirty-tree-guard] Blocked: {reason}. Commit first, or ask the user.",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
