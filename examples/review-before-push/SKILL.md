---
name: review-before-push
description: Review the committed work before a push to main or a deploy. Runs separate code, simplicity and security reviews, fixes what must be fixed, reviews the fixes, runs the project's checks on the exact commit, and only then stamps that commit with bin/mark-reviewed. Use once the user has asked for a push to main or a deploy.
---

# Review before push

The push and deploy guards refuse any commit but the one this skill stamps.
Run it once the user has asked for a push to main or a deploy; never push
uninvited.

## 1. Pre-flight

- Record `git rev-parse HEAD`: this is the commit under review.
- The diff is `origin/main...HEAD`. Empty: stop and say so.
- Uncommitted changes do not block, and are not reviewed: the stamp binds to
  a commit. List dirty and untracked paths in the report as "not reviewed"
  and leave them alone: never edit, stash or commit them. If a fix needs one
  of those files, stop and ask the user.

## 2. Review passes

Run each as its own skill or subagent with fresh context, in this order, and
do not re-review the diff yourself in between:

1. `/code-review` for correctness.
2. `/simplify` for reuse and duplication: the helper rewritten although it
   already exists.
3. `/security-review`.

Ask each pass for every finding, then filter here. A reviewer told to report
only what is severe reports less than it saw.

## 3. Fix, then review the fixes

Must-fix: anything severe, and any confirmed medium. Show the other mediums
to the user and fix them unless deferred. Note the rest.

Commit the fixes (only files the review changed). Review again, on just
those commits, and repeat until a pass leaves nothing that must be fixed.

## 4. Checks on the commit itself

Run the project's full checks against HEAD, not the working tree, so
uncommitted files can neither hide nor cause a failure:
`git worktree add --detach <scratch>/review-<sha> HEAD`, run them there,
then `git worktree remove` it.

## 5. Stamp

Only when the last pass left no must-fix and the checks pass on HEAD:

```bash
bin/mark-reviewed
```

One more commit moves HEAD and voids the stamp: start again from step 1.

## 6. Report

The commit and how many files were reviewed, what was fixed, what was
deferred and why, the paths not reviewed, and `stamped: <sha>` or
`not stamped: <reason>`.

## No bypass

Do not write `.claude/.review-passed` any other way, and do not switch git's
hooks off. In an emergency the user edits the file by hand and owns that
decision.
