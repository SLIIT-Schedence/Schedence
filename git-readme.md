# Schedence — Git Workflow Guide

A simple Git guide for all Schedence team members. Our goal is to keep `main` stable, make changes easy to review, and maintain a clean Git history.

> **Our workflow:** Create `feat/` or `fix/` branch → commit → push → open a Pull Request (PR) → request a reviewer → get approval → **Squash and merge**.
>
> We use **`main` + short-lived feature/fix branches**. We do not need a separate `dev` branch right now.

## 1. Team rules

1. **Never push directly to `main`.** All changes go through a Pull Request.
2. **Branch names must begin with `feat/` or `fix/`** and use lowercase words separated by hyphens.
3. **Use meaningful commit messages:** `type: short description`.
4. **Every PR must have a reviewer requested** and receive at least **one approval** before merging.
5. **Use “Squash and merge” only** when merging a PR into `main`.
6. Keep each PR focused on **one feature or one bug fix**.
7. Do not force-push or rebase `main`. Never rewrite a branch that multiple people are using without coordinating.
8. After a PR is merged, delete the finished branch.

### Branch examples

```text
feat/lecturer-management
feat/exam-timetable
feat/staff-duty-allocation
fix/login-validation
fix/room-overlap
```

**Avoid:** `my-work`, `feature/login`, `test123`, `kalana-final`.

### Commit message examples

```text
feat: add lecturer availability form
fix: prevent duplicate room bookings
docs: update project setup instructions
refactor: simplify timetable validation
test: add exam conflict test cases
chore: update dependencies
```

**Avoid:** `update`, `done`, `final`, `changes`, `bug fix`.

---

## 2. First-time setup — clone the repository

Do this once on your computer. Replace the example URL with our actual repository URL.

```bash
git clone https://github.com/ORG/REPO.git
cd REPO

git switch main
git pull --ff-only origin main
```

If Git asks you to identify yourself (one-time setup):

```bash
git config --global user.name "Your Name"
git config --global user.email "your-github-email@example.com"
```

---

## 3. Scenario A — Start a new feature or bug fix

**Example:** You want to build the lecturer management page.

**Step 1 — Start from the latest `main`:**

```bash
git switch main
git pull --ff-only origin main
```

**Step 2 — Create and enter your own branch:**

```bash
git switch -c feat/lecturer-management
```

**Step 3 — Make your code changes, then check them:**

```bash
git status
git diff
```

**Step 4 — Stage and commit your changes:**

```bash
git add .
git commit -m "feat: add lecturer management page"
```

`git add .` stages all changes in the current directory. Review `git status` before committing so you do not accidentally include temporary files or secrets.

**Step 5 — Push your branch to GitHub:**

```bash
git push -u origin feat/lecturer-management
```

**Step 6 — Open a PR and request a reviewer:**

1. Open our repository on GitHub.
2. Click **Compare & pull request** (or **Pull requests → New pull request**).
3. Set **base: `main`** and **compare: `feat/lecturer-management`**.
4. Add a clear PR title, for example `feat: add lecturer management page`.
5. Briefly describe what changed and how you tested it.
6. In the **Reviewers** section on the right, select a teammate or the team leader.
7. Click **Create pull request**. If you cannot find the reviewer picker, ask the team leader to check repository access.
8. Wait for **at least one approval**. Address review comments before merging.

> **Important:** Requesting a reviewer is a team rule. GitHub's “Require approvals” setting alone does not force someone to select a reviewer when they first create a PR.

---

## 4. Scenario B — Continue working on your existing branch

**Example:** You already pushed `feat/lecturer-management` yesterday and want to continue today.

```bash
git switch feat/lecturer-management

git status
git pull --ff-only origin feat/lecturer-management
```

Make more changes, then:

```bash
git add .
git commit -m "feat: add lecturer availability validation"
git push
```

Because `-u` was used on the first push, `git push` is enough for later pushes.

**If you are the only person working on this branch**, you usually will not need `git pull` from the same feature branch. It is useful when you have also edited that branch from another computer.

---

## 5. Scenario C — Get the latest `main` changes into your feature branch (rebase)

**Example:** Another member has merged new code into `main`, and you want that code in your branch **without a merge commit**.

First, save your work with a commit (or stash it, as explained in Scenario D).

```bash
git switch feat/lecturer-management
git fetch origin
git rebase origin/main
```

If the branch has **already been pushed** and the rebase changed its commits:

```bash
git push --force-with-lease origin feat/lecturer-management
```

If the branch has **never been pushed**, use:

```bash
git push -u origin feat/lecturer-management
```

**Why `--force-with-lease`?** A rebase rewrites your feature branch's commit IDs. This option is safer than plain `--force` because it refuses to overwrite remote commits you have not fetched. Use it **only on your own feature/fix branch**, never on `main` or a shared branch.

> Rebase **before requesting final PR approval** where possible. Updating commits after approval may require a new review, depending on repository settings.

---

## 6. Scenario D — Get updates while you have uncommitted changes

Rebase works most reliably with a clean working tree. If you are not ready to commit yet, temporarily save your changes:

```bash
git status
git stash push -u -m "WIP before rebase"

git fetch origin
git rebase origin/main

git stash pop
```

If `stash pop` reports conflicts, fix the files and stage them with `git add <file>` as normal. Review your changes with `git status`.

---

## 7. Scenario E — Resolve a rebase conflict

If you see something like:

```text
CONFLICT (content): Merge conflict in scheduler.py
```

**Step 1 — See the conflicted files:**

```bash
git status
```

**Step 2 — Open each file and resolve the conflict.** Remove conflict markers such as `<<<<<<<`, `=======`, and `>>>>>>>`, keeping the correct code.

**Step 3 — Stage resolved files and continue:**

```bash
git add scheduler.py
git rebase --continue
```

Repeat if Git finds another conflict. When the rebase is complete, run your tests and push your own branch using `--force-with-lease` if necessary.

**Cancel and return to where you started:**

```bash
git rebase --abort
```

Do **not** run `git rebase --skip` unless you understand which commit would be dropped.

---

## 8. Scenario F — Reviewer requests changes to your PR

You do **not** need to open a new PR. Push new commits to the **same branch**, and the existing PR updates automatically.

```bash
git switch feat/lecturer-management

# Make the requested code changes

git add .
git commit -m "fix: address lecturer form review comments"
git push
```

Reply to the review comments on GitHub and request a re-review if necessary. Wait for approval before merging.

---

## 9. Scenario G — After your PR is approved and merged

The team leader (or another authorized maintainer) selects **Squash and merge** on GitHub. After the PR is merged, update your local `main` and delete your finished local branch:

```bash
git switch main
git pull --ff-only origin main
git fetch --prune

git branch -D feat/lecturer-management
```

`-D` is used here only **after confirming the PR was squash-merged**, because Git may not recognize the original feature commits as merged. Do not use `-D` on unfinished work.

If GitHub is configured to **automatically delete head branches**, the remote feature branch will already be removed.

---

## 10. Scenario H — Check your branches and Git graph

```bash
# Current branch and working tree
git status

# Local branches
git branch

# Local and remote branches
git branch -a

# Complete commit graph
git log --oneline --graph --decorate --all

# Simple main-branch history
git log --oneline --first-parent main
```

With **Squash and merge**, `main` stays easy to read: each completed PR normally appears as one descriptive commit.

---

## 11. Simple PR template to copy

**PR title:**

```text
feat: add lecturer management page
```

**PR description:**

```markdown
## What changed?
- Added lecturer management page
- Added form validation

## How did you test it?
- Opened the page locally
- Tested creating and updating lecturer information

## Reviewer
- Requested review from: @github-username
```

Choose the reviewer using GitHub's **Reviewers** control too. Merely typing a username in the PR description does **not** send an official review request.

---

## 12. Daily quick reference

### New task

```bash
git switch main
git pull --ff-only origin main
git switch -c feat/my-task
# Work...
git add .
git commit -m "feat: describe what you implemented"
git push -u origin feat/my-task
# Open PR to main → select reviewer → wait for approval
```

### Update your feature branch with the latest main

```bash
git switch feat/my-task
git fetch origin
git rebase origin/main
# If already pushed and commits were rewritten:
git push --force-with-lease origin feat/my-task
```

### Fix a PR after review

```bash
git switch feat/my-task
# Fix code...
git add .
git commit -m "fix: address review feedback"
git push
```

### After squash merge

```bash
git switch main
git pull --ff-only origin main
git fetch --prune
git branch -D feat/my-task
```

**Remember:** Never commit directly to `main`. Always: **Branch → Commit → Push → PR → Reviewer → Approval → Squash and merge**.
