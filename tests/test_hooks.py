#!/usr/bin/env python3
"""Tests for the two agent hooks, the pre-push hook and mark-reviewed.

Stdlib only. Run: python3 -m unittest discover tests -v

Most of the command forms below were found by review agents reading earlier
versions of the guard: each one once got a push or a deploy past it.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REVIEW_GUARD = ROOT / "hooks" / "review_guard.py"
DIRTY_GUARD = ROOT / "hooks" / "dirty_tree_guard.py"
PRE_PUSH_DIR = ROOT / "githooks"
MARK_REVIEWED = ROOT / "bin" / "mark-reviewed"

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}
ALLOW, BLOCK = 0, 2


def git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=False,
        env=GIT_ENV,
    )


def commit(repo: str, message: str = "change") -> str:
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def run_hook(script: Path, command: str, cwd: str, project_dir: str = "") -> int:
    """Exit code of a hook given one Bash tool call from a session in `cwd`."""
    payload = {"cwd": cwd, "tool_input": {"command": command}}
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CLAUDE_PROJECT_DIR": project_dir or cwd},
        timeout=15,
    ).returncode


class Repos(unittest.TestCase):
    """One reviewed checkout and one that never was."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="hook-test-")
        self.addCleanup(self.tmp.cleanup)
        self.reviewed = self.make_repo("reviewed")
        self.unreviewed = self.make_repo("unreviewed")
        self.mark(self.reviewed)

    def make_repo(self, name: str) -> str:
        # realpath: git reports the resolved path, and macOS tmp is a symlink.
        path = os.path.join(os.path.realpath(self.tmp.name), name)
        subprocess.run(["git", "init", "-q", "-b", "main", path], check=True)
        (Path(path) / ".gitignore").write_text(".claude/.review-passed\n")
        git(path, "add", ".gitignore")
        commit(path, "init")
        return path

    def mark(self, repo: str) -> None:
        marker = Path(repo) / ".claude" / ".review-passed"
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(git(repo, "rev-parse", "HEAD").stdout)


class ReviewGuard(Repos):
    def test_blocks_every_form_from_an_unreviewed_checkout(self) -> None:
        forms = {
            "a plain push": "git push origin main",
            "a refspec": "git push origin HEAD:main",
            "a forced push": "git push origin +main",
            "a push after an unspaced ;": "git status;git push origin main",
            "a push after a comment line": "# deploy\ngit push origin main",
            "a push after a mid-word #": "echo a#b; git push --no-verify origin main",
            "a push inside bash -c": "bash -c 'git push origin main'",
            "a push on a bash -c second line": "bash -c 'echo hi\ngit push origin main'",
            "a push in a subshell": "(git push origin main) && echo $'it\\'s'",
            "a push of quoted main": "git push origin \"main\" && echo $'it\\'s'",
            "a push inside sh -c": "sh -c \"echo a#b $'x\\'y'; git push origin main\"",
            "a push over a line continuation": "git push \\\n  origin main",
            "a refspec after a line continuation": "git push origin \\\n  main",
            "an escaped letter in push": "git pu\\sh origin main",
            "a redirect glued to main": "git push origin main>/dev/null 2>&1",
            "an upper-case GIT": "GIT push origin main",
            "a bare push that skips git's hooks": "git push --no-verify",
            "a feature push that skips git's hooks": "git push --no-verify origin feature",
            "a push with hooksPath switched off": "git -c core.hooksPath=/dev/null push origin HEAD",
            "the same with the config quoted": 'git -c "core.hookspath=" push',
            "hooksPath switched off through the environment": "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0= git push",
            "a deploy": "gh workflow run deploy.yml",
            "a deploy on a later line": "git status\ngh workflow run deploy.yml -f target=prod",
            "a deploy after cd": "cd x && gh workflow run deploy.yml",
            "a deploy after $(...);": "echo $(date);gh workflow run deploy.yml",
            "a deploy with a quote in its comment": "gh workflow run deploy.yml # don't watch",
            "a deploy by display name": 'gh workflow run "Deploy"',
            "a flag before the workflow": "gh workflow run --ref main deploy.yml",
            "an input before the workflow": "gh workflow run -f target=both deploy.yml",
            "-R after the subcommand": "gh workflow -R o/r run deploy.yml",
            "a dispatch through gh api": "gh api repos/o/r/actions/workflows/deploy.yml/dispatches -f ref=main",
            "a dispatch through curl": "curl -X POST https://api.github.com/repos/o/r/actions/workflows/deploy.yml/dispatches",
            "a combined -lc wrapper": "bash -lc 'gh workflow run deploy.yml'",
            "bash -c --": "bash -c -- 'gh workflow run deploy.yml'",
            "an env prefix": "env gh workflow run deploy.yml",
            "a lowercase env assignment": "foo=bar gh workflow run deploy.yml",
            "a sudo prefix": "sudo -E gh workflow run deploy.yml",
            "a sudo -u prefix": "sudo -u root gh workflow run deploy.yml",
            "a command prefix": "command gh workflow run deploy.yml",
            "eval": "eval 'gh workflow run deploy.yml'",
            "a subshell": "(gh workflow run deploy.yml)",
            "a brace group": "{ gh workflow run deploy.yml; }",
            "an if body": "if true; then gh workflow run deploy.yml; fi",
            "a negation": "! gh workflow run deploy.yml",
            "a command substitution": "x=$(gh workflow run deploy.yml)",
            "a line piped into bash": "echo 'gh workflow run deploy.yml' | bash",
            "xargs": "echo deploy.yml | xargs gh workflow run",
            "a deploy over a line continuation": "gh workflow \\\n  run deploy.yml",
            "a quoted part of the workflow name": 'gh workflow run "dep"loy.yml',
            "an escaped letter in the verb": "gh workflow r\\un deploy.yml",
        }
        for label, command in forms.items():
            with self.subTest(label):
                self.assertEqual(
                    run_hook(REVIEW_GUARD, command, self.unreviewed), BLOCK
                )

    def test_allows_the_same_from_the_reviewed_checkout(self) -> None:
        for command in [
            "git push origin main",
            "git status\ngh workflow run deploy.yml -f target=prod",
            "gh workflow run deploy.yml # don't watch",
            "gh workflow run deploy.yml && echo $'it\\'s'",
        ]:
            with self.subTest(command):
                self.assertEqual(run_hook(REVIEW_GUARD, command, self.reviewed), ALLOW)

    def test_one_more_commit_voids_the_review(self) -> None:
        commit(self.reviewed, "a small fix after the review")
        self.assertEqual(
            run_hook(REVIEW_GUARD, "git push origin main", self.reviewed), BLOCK
        )

    def test_leaves_other_commands_alone(self) -> None:
        for command in [
            "git push origin feature",
            "git push --dry-run origin feature",
            "git push -n origin feature",
            "git config core.hooksPath githooks",
            "git config core.hooksPath githooks  # so pre-push runs",
            "gh workflow run tests.yml",
            "ls deploy/",
            "cat <<'EOF'\nit's the git push step\nEOF",
        ]:
            with self.subTest(command):
                self.assertEqual(
                    run_hook(REVIEW_GUARD, command, self.unreviewed), ALLOW
                )

    def test_reads_the_marker_of_the_session_checkout_not_the_project_dir(self) -> None:
        # A worktree session: cwd is its own checkout; the project dir is another.
        push = "git push origin main"
        self.assertEqual(
            run_hook(REVIEW_GUARD, push, self.reviewed, self.unreviewed), ALLOW
        )
        self.assertEqual(
            run_hook(REVIEW_GUARD, push, self.unreviewed, self.reviewed), BLOCK
        )

    def test_blocks_a_push_of_another_checkout_the_line_names(self) -> None:
        unrev = shlex.quote(self.unreviewed)
        rev = shlex.quote(self.reviewed)
        forms = {
            "git -C": f"git -C {unrev} push origin main",
            "an unrelated git -C before the push": f"git -C {rev} status && git -C {unrev} push origin main",
            "-c before -C": f"git -c a.b=c -C {unrev} push origin main",
            "--no-pager before -C": f"git --no-pager -C {unrev} push origin main",
            "stacked -C": f"git -C {rev} -C {unrev} push origin main",
            "git -C inside bash -c": f"bash -c 'git -C {unrev} push origin main'",
            "cd then push": f"cd {unrev} && git push origin main",
            "pushd then push": f"pushd {unrev} && git push origin main",
            "--git-dir": f"git --git-dir={unrev}/.git push origin main",
            "a GIT_DIR prefix": f"GIT_DIR={unrev}/.git git push origin main",
        }
        for label, command in forms.items():
            with self.subTest(label):
                self.assertEqual(run_hook(REVIEW_GUARD, command, self.reviewed), BLOCK)

    def test_a_cd_that_never_runs_does_not_lend_its_review(self) -> None:
        command = f"false && cd {shlex.quote(self.reviewed)}; git push origin main"
        self.assertEqual(run_hook(REVIEW_GUARD, command, self.unreviewed), BLOCK)

    def test_allows_naming_the_reviewed_checkout(self) -> None:
        rev = shlex.quote(self.reviewed)
        for command in [
            f"git -C {rev} push origin HEAD:main",
            f"cd -P {rev} && git push origin main",
        ]:
            with self.subTest(command):
                self.assertEqual(run_hook(REVIEW_GUARD, command, self.reviewed), ALLOW)


class DirtyTreeGuard(Repos):
    DESTRUCTIVE = [
        "git checkout .",
        "git checkout -- src/app.py",
        "git checkout main",
        "git restore src/app.py",
        "git restore --staged --worktree src/app.py",
        "git restore -SW src/app.py",
        "git reset --hard",
        "git reset -q --hard",
        "git reset HEAD --hard",
        "git clean -fd",
        "git switch -f main",
        "git switch --discard-changes main",
        'git "checkout" .',
        "git \\\n  checkout .",
        "git -C . checkout .",
        "git -c a.b=c checkout .",
        "GIT checkout .",
        "sudo git checkout .",
        "npm test && git checkout .",
        "git status\ngit checkout .",
        "git checkout -b fix && git checkout .",
        "bash -c 'git checkout .'",
        "eval 'git reset --hard'",
        "x=$(git reset --hard)",
        "(git clean -fd)",
        "echo . | xargs git checkout",
        "git checkout . && echo $'it\\'s'",
        'echo "$(git checkout . 2>&1)"',
        'OUT="$(git reset --hard 2>&1)"',
        "echo a#b; git reset --hard",
    ]
    HARMLESS = [
        "git checkout -b feature",
        "git checkout -q -b feature",
        "git checkout --track -b feature origin/feature",
        "git checkout -B feature",
        "git checkout --theirs f.txt",
        "git checkout --ours f.txt",
        "git restore --staged f.txt",
        "git reset --soft HEAD~1",
        "git reset HEAD f.txt",
        "git clean -n",
        "git clean -nfd",
        "git switch main",
        "git switch -c feature",
        "git status",
        "git stash",
        "git stash pop",
        'git commit -am "fix checkout flow"',
        'git commit -m "undo the git reset --hard in deploy.sh"',
        "git grep checkout",
        "git log --oneline -- restore.py",
        "echo checkout",
    ]
    NEVER = [
        "git stash drop",
        "git stash clear",
        "git stash drop stash@{0}",
        "git clean -fdx",
        "git clean -f -X",
    ]

    def dirty(self) -> str:
        (Path(self.unreviewed) / "edit.txt").write_text("uncommitted\n")
        return self.unreviewed

    def test_blocks_destructive_commands_on_a_dirty_tree(self) -> None:
        repo = self.dirty()
        for command in self.DESTRUCTIVE:
            with self.subTest(command):
                self.assertEqual(run_hook(DIRTY_GUARD, command, repo), BLOCK)

    def test_allows_them_on_a_clean_tree(self) -> None:
        for command in self.DESTRUCTIVE:
            with self.subTest(command):
                self.assertEqual(run_hook(DIRTY_GUARD, command, self.unreviewed), ALLOW)

    def test_allows_harmless_commands_on_a_dirty_tree(self) -> None:
        repo = self.dirty()
        for command in self.HARMLESS:
            with self.subTest(command):
                self.assertEqual(run_hook(DIRTY_GUARD, command, repo), ALLOW)

    def test_refuses_what_no_commit_holds_even_on_a_clean_tree(self) -> None:
        # A stash, and ignored files: neither makes the tree dirty.
        for command in self.NEVER:
            with self.subTest(command):
                self.assertEqual(run_hook(DIRTY_GUARD, command, self.unreviewed), BLOCK)


class PrePushAndMark(Repos):
    def setUp(self) -> None:
        super().setUp()
        self.remote = os.path.join(os.path.realpath(self.tmp.name), "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", self.remote], check=True)
        for repo in (self.reviewed, self.unreviewed):
            git(repo, "remote", "add", "origin", self.remote)
            git(repo, "config", "core.hooksPath", str(PRE_PUSH_DIR))

    def push(self, repo: str, *args: str) -> int:
        return git(repo, "push", "-q", "origin", *args).returncode

    def test_refuses_an_unreviewed_commit_on_main(self) -> None:
        self.assertNotEqual(self.push(self.unreviewed, "main"), 0)

    def test_accepts_the_reviewed_commit(self) -> None:
        self.assertEqual(self.push(self.reviewed, "main"), 0)

    def test_refuses_a_commit_made_after_the_review(self) -> None:
        commit(self.reviewed, "a small fix after the review")
        self.assertNotEqual(self.push(self.reviewed, "main"), 0)

    def test_refuses_a_bare_push_which_the_text_guard_cannot_see(self) -> None:
        git(self.unreviewed, "config", "push.default", "current")
        self.assertEqual(run_hook(REVIEW_GUARD, "git push", self.unreviewed), ALLOW)
        self.assertNotEqual(git(self.unreviewed, "push", "-q", "origin").returncode, 0)

    def test_the_text_guard_stops_a_push_that_would_skip_this_hook(self) -> None:
        skip = "git -c core.hooksPath=/dev/null push origin HEAD"
        self.assertEqual(run_hook(REVIEW_GUARD, skip, self.unreviewed), BLOCK)

    def test_says_why_without_a_shell_error_when_nothing_was_reviewed(self) -> None:
        refused = git(self.unreviewed, "push", "-q", "origin", "main").stderr
        self.assertIn("the review is for no commit", refused)
        self.assertNotIn("No such file", refused)

    def test_leaves_other_branches_alone(self) -> None:
        git(self.unreviewed, "switch", "-q", "-c", "feature")
        self.assertEqual(self.push(self.unreviewed, "feature"), 0)

    def test_mark_reviewed_stamps_head_and_the_push_passes(self) -> None:
        head = commit(self.unreviewed, "work")
        done = subprocess.run(
            [str(MARK_REVIEWED)], cwd=self.unreviewed, capture_output=True, check=False
        )
        self.assertEqual(done.returncode, 0)
        marker = Path(self.unreviewed) / ".claude" / ".review-passed"
        self.assertEqual(marker.read_text().strip(), head)
        self.assertEqual(self.push(self.unreviewed, "main"), 0)

    def test_mark_reviewed_refuses_a_dirty_tree(self) -> None:
        (Path(self.unreviewed) / "edit.txt").write_text("uncommitted\n")
        done = subprocess.run(
            [str(MARK_REVIEWED)], cwd=self.unreviewed, capture_output=True, check=False
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertFalse((Path(self.unreviewed) / ".claude" / ".review-passed").exists())


if __name__ == "__main__":
    unittest.main()
