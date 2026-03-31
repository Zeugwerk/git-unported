# git-unported

Tooling that lists **commits present on an integration branch (for example `main`) but not on a release branch (for example `release/1.6`)**, in a form you can use for **release notes** and **backport checklists**. Implemented as **`git_unported.py`** (Python 3, stdlib only; shells out to `git`).

It runs in the **top-level Git repo** and, when `.gitmodules` exists, repeats the same check for **each first-level submodule**.

<img width="1193" height="352" alt="Screenshot_20260331_224234" src="https://github.com/user-attachments/assets/bbba0005-a6ab-4688-8232-e033a3313bdc" />

## When this is useful (release flow)

Typical pattern:

- Day-to-day work lands on **`main`** (or `develop`).
- Fixes and selected changes are **cherry-picked** (or otherwise applied) onto **`release/x.y`** for shipping.
- Before tagging or publishing, you want to know:
  - **What is still only on `main`?** (candidates for the next patch release or for “known gaps” in the release notes.)
  - **Did we already cherry-pick something?** (so you do not duplicate work.)

The tool answers the first question **per repository** (superproject + each submodule), while **hiding commits that already appear as cherry-picks on the release branch** when those cherry-picks were done with **`git cherry-pick -x`** (so the trailer `(cherry picked from commit <hash>)` is present in the release branch history).

## Requirements

- **Python:** 3.9+
- **Git:** `git rev-parse`, `git log`, `git patch-id`, submodule layout

## Setup

Put `git_unported.py` on your `PATH` (or run it by path) and ensure it is executable if you want `./git_unported.py`:

```bash
chmod +x git_unported.py
./git_unported.py main release/1.6 origin
# or: python3 git_unported.py -c -F main release/1.6 origin
```

Environment variables: `BODY_LINES_MAX`, `SUBJECT_DEDUP`, `PATCH_ID_INDEX_MAX`, `NO_COLOR`, `COLUMNS`.

Run it from **any directory inside the repository** (including inside a submodule checkout). If Git reports a **superproject** and that superproject has a `.gitmodules` file, the script anchors on the superproject so **all sibling submodules** are still scanned.

## Usage

```text
git_unported.py [options] [main-branch] [release-branch] [remote]
```

| Argument / flag | Default | Role |
|-----------------|---------|------|
| `--conventional-only`, `-c` | off | Omit the **Other commits** block; repos with only non-conventional unported commits print **no** section for that repo. |
| `--feat-fix-only`, `-F` | off | Show only **`feat:`** and **`fix:`** (including `feat!` / `fix!` and optional scope). Hides `docs:`, `chore:`, unprefixed commits, and all other types. |
| `--verbose`, `-v` | off | Print candidate counts and filter outcomes on **stderr** (useful when output is empty). |
| `main-branch` | `main` | “Source” branch (where new work lives) |
| `release-branch` | `release/1.0` | “Target” branch (what you ship / maintain) |
| `remote` | `origin` | Used for `git fetch` and for `origin/<branch>` refs when they exist |

Flags may appear **before** the branch arguments (recommended).

Examples:

```bash
# Typical: what’s on main but not on the 1.6 release line?
git_unported.py main release/1.6 origin

# Same, but only conventional commits (no “Update foo.md” / unprefixed lines)
git_unported.py --conventional-only main release/1.6 origin

# Only new features and fixes (release-notes style)
git_unported.py --feat-fix-only main release/1.6 origin

# Shorter if defaults match your repo
git_unported.py
```

The script tries **`$remote/$branch`** first (e.g. `origin/main`); if that ref does not exist, it falls back to the **local** branch name (`main`, `release/1.6`). It runs `git fetch "$remote"` best-effort and prints fetch and other Git failures on **stderr**.

## Output

For each repo that has **at least one** matching commit, you get a table header (`hash`, `date`, `author`, `message`) and then commits grouped for **release notes**:

1. **Conventional commits:** first line matches [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix(scope):`, `docs:`, `chore!:`, …), with a small tolerance for a **space before the scope** (e.g. `fix (PowerMeasurementEL34x3): …`). The **type** is color-coded (for example `feat` / `fix` / `doc` / `ci` / `chore`). Lines after the subject come from the commit body: **blank lines are skipped**, lines that are only `(cherry picked from commit …)` are omitted (common when that text was copied into the message), leading/trailing empty body text is ignored, and only the **first few non-blank body lines** are shown (default 4), then `… (truncated)` if there is more. Set **`BODY_LINES_MAX`** if you want more or fewer lines. Commits are printed **one after another with no blank line** between entries.

2. **Other commits:** anything without that prefix (often mechanical one-liners like “Update toc.yml”) is listed **after** the conventional block, fully dimmed, so the important items read first.

If a repo only has conventional commits (or only non-conventional ones), the script omits the redundant subsection heading and prints a single list.

Sections with **no** unported commits produce **no** output (by design).

Commit bodies may contain any character except ASCII **record separator** (`0x1E`), which the script uses internally when reading `git log` output (extremely unlikely in normal messages).

## How “unported” is defined

1. **Candidates:** commits reachable from the main branch that are **not** reachable from the release branch (same idea as `git log main --not release/...`).

2. **Drop if `-x` says so (takes precedence):** scan the **entire** history of the release branch for `(cherry picked from commit <hash>)`. If a candidate’s hash **matches** one of those (prefix match on the first 12 hex characters of the full hash), it is **dropped**. If this rule disagrees with patch-id below, **this rule wins**.

3. **Drop if the same patch and subject appear on release:** the script scans the release branch and builds a map from **stable patch-id** (`git patch-id --stable`) to **`%s` subject lines**. A candidate is **dropped** if its patch-id matches and its **normalized** first line matches one of those subjects (trimmed, CR stripped, lowercased, internal whitespace collapsed). Step 2 still wins on conflict.

4. **Drop if the same subject line already exists on release (default on):** while scanning release, every commit’s `%s` is added to a set of **normalized** subjects. If the candidate’s normalized first line is in that set, it is **dropped**. Normalization includes lowercasing, trimming, collapsing whitespace, and stripping trailing **`(#123)`**-style PR/issue suffixes so `fix: foo` and `fix: foo (#235)` match. This catches cherry-picks and rebases where the **patch-id differs** but the title is the same. Set **`SUBJECT_DEDUP=0`** to turn this off if you rely on repeating identical subjects for different changes.

So:

- Cherry-picks **with `-x`** still hide the source commit by hash when the trailer matches, even if steps 3–4 would disagree.
- Steps 3–4 reduce false “still unported” when the change or the same title is already on release under a different commit object.

**Large repos:** set `PATCH_ID_INDEX_MAX` to a positive number to only scan that many **newest** commits on the release branch when building the maps (older matches may be missed).

## Submodules

- Only **first-level** entries in the superproject’s `.gitmodules` are processed; **nested** submodules inside those repos are not recursed automatically.
- If a path from `.gitmodules` is **not** a valid Git checkout (submodule never initialized), you’ll see a **skipping submodule … not a git checkout** line.

## Limitations (good to know)

- **Normalized subject dedup** can hide a second legitimate commit that reuses the exact same title as an older one on release; use `SUBJECT_DEDUP=0` if that bites you.
- **Merge-heavy** histories can make “not in release” mean something subtler than “this single commit isn’t there”; the underlying logic is Git’s reachability (`--not`), not a semantic diff of patches.
