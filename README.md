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
git-unported [--conventional-only|-c] [main-branch] [release-branch] [remote]
```

| Argument / flag | Default | Role |
|-----------------|---------|------|
| `--conventional-only`, `-c` | off | Omit the **Other commits** block; repos with only non-conventional unported commits print **no** section for that repo. |
| `main-branch` | `main` | “Source” branch (where new work lives) |
| `release-branch` | `release/1.0` | “Target” branch (what you ship / maintain) |
| `remote` | `origin` | Used for `git fetch` and for `origin/<branch>` refs when they exist |

Flags may appear **before** the branch arguments (recommended).

Examples:

```bash
# Typical: what’s on main but not on the 1.6 release line?
git-unported main release/1.6 origin

# Same, but only conventional commits (no “Update foo.md” / unprefixed lines)
git-unported --conventional-only main release/1.6 origin

# Shorter if defaults match your repo
git-unported
```

The script tries **`$remote/$branch`** first (e.g. `origin/main`); if that ref does not exist, it falls back to the **local** branch name (`main`, `release/1.6`). It runs `git fetch "$remote"` best-effort (errors ignored) so remote-tracking branches are often up to date.

## Output

For each repo that has **at least one** matching commit, you get a table header (`hash`, `date`, `author`, `message`) and then commits grouped for **release notes**:

1. **Conventional commits** — first line matches [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix(scope):`, `docs:`, `chore!:`, …), with a small tolerance for a **space before the scope** (e.g. `fix (PowerMeasurementEL34x3): …`). The **type** is color-coded (for example `feat` / `fix` / `doc` / `ci` / `chore`). Lines after the subject come from the commit body: **blank lines are skipped**, lines that are only `(cherry picked from commit …)` are omitted (common when that text was copied into the message), leading/trailing empty body text is ignored, and only the **first few non-blank body lines** are shown (default 4), then `… (truncated)` if there is more. Adjust `BODY_LINES_MAX` at the top of the script if you want more or fewer lines. Commits are printed **one after another with no blank line** between entries.

2. **Other commits** — anything without that prefix (often mechanical one-liners like “Update toc.yml”) is listed **after** the conventional block, fully dimmed, so the important items read first.

If a repo only has conventional commits (or only non-conventional ones), the script omits the redundant subsection heading and prints a single list.

Sections with **no** unported commits produce **no** output (by design).

Commit bodies may contain any character except ASCII **record separator** (`0x1E`), which the script uses internally when reading `git log` output (extremely unlikely in normal messages).

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