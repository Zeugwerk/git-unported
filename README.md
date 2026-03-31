# git-unported

Small Bash helper that lists **commits present on an integration branch (for example `main`) but not on a release branch (for example `release/1.6`)**, in a form you can use for **release notes** and **backport checklists**.

It runs in the **top-level Git repo** and, when `.gitmodules` exists, repeats the same check for **each first-level submodule**.

## When this is useful (release flow)

Typical pattern:

- Day-to-day work lands on **`main`** (or `develop`).
- Fixes and selected changes are **cherry-picked** (or otherwise applied) onto **`release/x.y`** for shipping.
- Before tagging or publishing, you want to know:
  - **What is still only on `main`?** — candidates for the next patch release or for “known gaps” in the release notes.
  - **Did we already cherry-pick something?** — avoid duplicating work.

`git-unported` answers the first question **per repository** (superproject + each submodule), while **hiding commits that already appear as cherry-picks on the release branch** when those cherry-picks were done with **`git cherry-pick -x`** (so the trailer `(cherry picked from commit <hash>)` is present in the release branch history).

## Requirements

- Bash
- Git (uses `git rev-parse`, `git log`, submodule layout)

## Setup

Put `git-unported` on your `PATH` and ensure it is executable:

```bash
chmod +x git-unported
# e.g. copy or symlink into ~/bin
```

Run it from **any directory inside the repository** (including inside a submodule checkout). If Git reports a **superproject** and that superproject has a `.gitmodules` file, the script anchors on the superproject so **all sibling submodules** are still scanned.

## Usage

```text
git-unported [main-branch] [release-branch] [remote]
```

| Argument | Default | Role |
|----------|---------|------|
| `main-branch` | `main` | “Source” branch (where new work lives) |
| `release-branch` | `release/1.0` | “Target” branch (what you ship / maintain) |
| `remote` | `origin` | Used for `git fetch` and for `origin/<branch>` refs when they exist |

Examples:

```bash
# Typical: what’s on main but not on the 1.6 release line?
git-unported main release/1.6 origin

# Shorter if defaults match your repo
git-unported
```

The script tries **`$remote/$branch`** first (e.g. `origin/main`); if that ref does not exist, it falls back to the **local** branch name (`main`, `release/1.6`). It runs `git fetch "$remote"` best-effort (errors ignored) so remote-tracking branches are often up to date.

## Output

For each repo that has **at least one** matching commit, you get a section with:

- short hash, date, author (truncated for column layout), **full commit subject** (no truncation — handy for copy-paste into release notes).

Sections with **no** unported commits produce **no** output (by design).

## How “unported” is defined

1. **Candidates:** commits reachable from the main branch that are **not** reachable from the release branch — same idea as `git log main --not release/...`.
2. **Cherry-pick filter:** from the **entire** history of the release branch (commit messages / bodies), collect hashes mentioned in lines like `(cherry picked from commit deadbeef...)`. Any candidate whose hash **matches** one of those (prefix match on the first 12 hex chars of the full hash) is **dropped** from the list.

So:

- Cherry-picks done **with `-x`** are treated as “already ported” for listing purposes.
- Cherry-picks **without** `-x`, or copies with rewritten messages, **still show up** as unported even if the patch is already on the release branch.

## Submodules

- Only **first-level** entries in the superproject’s `.gitmodules` are processed; **nested** submodules inside those repos are not recursed automatically.
- If a path from `.gitmodules` is **not** a valid Git checkout (submodule never initialized), you’ll see a **skipping submodule … not a git checkout** line.

## Limitations (good to know)

- Relies on **`cherry-pick -x`** trailers for the “already ported” heuristic; other workflows may need manual interpretation.
- **Merge-heavy** histories can make “not in release” mean something subtler than “this single commit isn’t there”; the underlying logic is Git’s reachability (`--not`), not a semantic diff of patches.