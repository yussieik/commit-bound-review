#!/usr/bin/env bash
# Installs the guards into the git repository you run it from:
#   cd your-repo && path/to/commit-bound-review/install.sh
# Copies the files (never over one that exists), ignores the marker, and
# points git at githooks/ unless this clone already has a hooks path.

set -euo pipefail

src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(git rev-parse --show-toplevel)"

put() { # put <from> <to>
  mkdir -p "$(dirname "$2")"
  if [ -e "$2" ]; then echo "kept    $2 (exists)"; else cp "$1" "$2" && echo "added   $2"; fi
}

put "$src/hooks/review_guard.py" .claude/hooks/review_guard.py
put "$src/hooks/dirty_tree_guard.py" .claude/hooks/dirty_tree_guard.py
put "$src/githooks/pre-push" githooks/pre-push
put "$src/bin/mark-reviewed" bin/mark-reviewed
put "$src/examples/review-before-push/SKILL.md" .claude/skills/review-before-push/SKILL.md

grep -qxF ".claude/.review-passed" .gitignore 2>/dev/null || echo ".claude/.review-passed" >>.gitignore

if [ -e .claude/settings.json ]; then
  echo "kept    .claude/settings.json: add the two PreToolUse hooks from $src/examples/settings.json"
else
  put "$src/examples/settings.json" .claude/settings.json
fi

hooks_path=$(git config --get core.hooksPath || true)
if [ -z "$hooks_path" ]; then
  git config core.hooksPath githooks && echo "set     core.hooksPath = githooks (this clone only)"
elif [ "$hooks_path" != githooks ]; then
  echo "kept    core.hooksPath = $hooks_path: call githooks/pre-push from your pre-push hook, with its arguments and stdin"
fi
echo "Commit these files. Every other clone runs: git config core.hooksPath githooks"
