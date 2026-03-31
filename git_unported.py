#!/usr/bin/env python3
"""
git-unported — commits on integration branch not yet on release.

-x exclusion, patch-id + normalized subject, subject dedup on release,
conventional / feat-fix filters, submodules.

Env: BODY_LINES_MAX, SUBJECT_DEDUP, PATCH_ID_INDEX_MAX, NO_COLOR, COLUMNS
Flags: -v/--verbose (diagnostics on stderr). Git errors are printed on stderr instead of being discarded.
Requires: Python 3.9+, git on PATH.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


def _use_color() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR", "").strip()


_ON = _use_color()


class C:
    BOLD = "\033[1m" if _ON else ""
    DIM = "\033[2m" if _ON else ""
    RESET = "\033[0m" if _ON else ""
    CYAN = "\033[36m" if _ON else ""
    YELLOW = "\033[33m" if _ON else ""
    GREEN = "\033[32m" if _ON else ""
    MAGENTA = "\033[35m" if _ON else ""
    RED = "\033[31m" if _ON else ""


RE_CONVENTIONAL = re.compile(r"^[a-z][a-z0-9]*\s*(\([^)]*\))?(!)?:\s*")
RE_CONV_PARTS = re.compile(
    r"^([a-z][a-z0-9]*)(\s*\([^)]*\))?(!)?(:)\s*(.*)$",
)
RE_FEAT_FIX = re.compile(r"^([a-z][a-z0-9]*)(\s*\([^)]*\))?(!)?:")
RE_CHERRY_BODY = re.compile(
    r"^\s*\(cherry\s+picked\s+from\s+commit\s+[0-9a-f]+\)\s*$",
    re.IGNORECASE,
)
RE_PORTED = re.compile(
    r"\(cherry picked from commit ([0-9a-f]{7,})\)",
    re.IGNORECASE,
)


def normalize_subject_key(s: str) -> str:
    t = s.replace("\r", "").lower()
    t = re.sub(r"^\s+|\s+$", "", t)
    t = re.sub(r"\s+", " ", t)
    while True:
        n = re.sub(r"\s*\(#[0-9]+\)\s*$", "", t)
        if n == t:
            break
        t = n
    return t


def strip_body_edges(s: str) -> str:
    t = s
    while t.startswith("\n"):
        t = t[1:]
    while t.endswith("\n"):
        t = t[:-1]
    return t


def is_conventional(first_line: str) -> bool:
    return bool(RE_CONVENTIONAL.match(first_line))


def is_feat_or_fix(first_line: str) -> bool:
    m = RE_FEAT_FIX.match(first_line)
    if not m:
        return False
    return m.group(1) in ("feat", "fix")


def format_conv_first_line(line: str) -> str:
    m = RE_CONV_PARTS.match(line)
    if not m:
        return line
    typ = m.group(1)
    scope_bang = (m.group(2) or "") + (m.group(3) or "") + (m.group(4) or "")
    desc = m.group(5) or ""
    tc = C.BOLD
    if typ == "feat":
        tc = C.BOLD + C.GREEN
    elif typ in ("fix", "perf"):
        tc = C.BOLD + C.YELLOW
    elif typ == "revert":
        tc = C.BOLD + C.RED
    elif typ in ("doc", "docs"):
        tc = C.BOLD + C.CYAN
    elif typ in ("test", "build", "ci"):
        tc = C.MAGENTA
    elif typ in ("chore", "style"):
        tc = C.DIM
    elif typ == "refactor":
        tc = C.CYAN
    return f"{tc}{typ}{C.RESET}{scope_bang} {desc}"


def git_run(
    cwd: str,
    args: List[str],
    *,
    stdin: Optional[bytes] = None,
    text: bool = False,
    hide_stderr: bool = False,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL if hide_stderr else subprocess.PIPE,
        text=text,
    )


def _git_stderr(r: subprocess.CompletedProcess) -> str:
    if r.stderr is None:
        return ""
    if isinstance(r.stderr, bytes):
        return r.stderr.decode(errors="replace").strip()
    return (r.stderr or "").strip()


def report_git_failure(what: str, cwd: str, r: subprocess.CompletedProcess) -> None:
    if r.returncode == 0:
        return
    err = _git_stderr(r)
    loc = cwd
    home = os.path.expanduser("~")
    if home and cwd.startswith(home):
        loc = "~" + cwd[len(home) :]
    if err:
        print(f"git-unported: {what} ({loc}): {err}", file=sys.stderr)
    else:
        print(
            f"git-unported: {what} ({loc}): exit {r.returncode}",
            file=sys.stderr,
        )


def git_ok(cwd: str, *args: str) -> bool:
    return git_run(cwd, list(args)).returncode == 0


def git_out(cwd: str, *args: str) -> str:
    r = git_run(cwd, list(args), text=True)
    if r.returncode != 0:
        return ""
    return r.stdout or ""


def resolve_ref(cwd: str, remote: str, branch: str) -> str:
    mb = f"{remote}/{branch}"
    if git_ok(cwd, "rev-parse", "--verify", mb):
        return mb
    return branch


def stable_patch_id(cwd: str, rev: str) -> Optional[str]:
    r = git_run(
        cwd,
        ["show", rev, "--no-textconv", "-p", "--pretty=format:"],
    )
    if r.returncode != 0 or not r.stdout:
        return None
    p = git_run(cwd, ["patch-id", "--stable"], stdin=r.stdout)
    if p.returncode != 0 or not p.stdout:
        return None
    line = p.stdout.decode(errors="replace").strip().splitlines()
    if not line:
        return None
    return line[0].split()[0]


def collect_ported_blob(cwd: str, rb: str) -> str:
    body = git_out(cwd, "log", rb, "--format=%B")
    hashes = RE_PORTED.findall(body)
    return "\n".join(hashes)


def build_release_maps(
    cwd: str, rb: str, patch_id_index_max: int
) -> Tuple[Dict[str, List[str]], Set[str], int]:
    """Index release branch: one git log for hash+subject, then patch-id per commit."""
    sep = "\x1e"
    log_args: List[str] = ["log"]
    if patch_id_index_max > 0:
        log_args.extend(["--max-count", str(patch_id_index_max)])
    # %x1e = RS in pretty format; --format=… must be one argv (Git misparses split --format).
    log_args.extend([rb, "-z", "--format=%H%x1e%s"])
    r = git_run(cwd, log_args)
    if r.returncode != 0:
        report_git_failure(f"git log {rb!r} (release index)", cwd, r)
        return {}, set(), 0
    raw = r.stdout.decode(errors="replace") if r.stdout else ""
    rel_patch_subj: Dict[str, List[str]] = {}
    rel_norm_subj: Set[str] = set()
    n_scanned = 0
    for rec in raw.split("\0"):
        if not rec:
            continue
        if sep not in rec:
            continue
        rh, subj = rec.split(sep, 1)
        rh = rh.strip()
        subj = subj.strip()
        if not rh:
            continue
        n_scanned += 1
        if not subj:
            continue
        nk = normalize_subject_key(subj)
        if nk:
            rel_norm_subj.add(nk)
        pid = stable_patch_id(cwd, rh)
        if not pid:
            continue
        rel_patch_subj.setdefault(pid, []).append(subj)
    return rel_patch_subj, rel_norm_subj, n_scanned


def parse_git_log_records(cwd: str, mb: str, rb: str, rs: str) -> List[Tuple[str, str, str, str]]:
    # rs (\x1e) separates parsed fields; Git needs %x1e in --format, not a raw RS byte.
    fmt = "%h%x1e%ad%x1e%an%x1e%B"
    r = git_run(
        cwd,
        [
            "log",
            mb,
            "--not",
            rb,
            "-z",
            f"--format={fmt}",
            "--date=short",
        ],
    )
    if r.returncode != 0:
        report_git_failure(f"git log {mb!r} --not {rb!r}", cwd, r)
        return []
    if not r.stdout:
        return []
    out = r.stdout.decode(errors="replace")
    rows: List[Tuple[str, str, str, str]] = []
    for rec in out.split("\0"):
        if not rec:
            continue
        parts = rec.split(rs, 3)
        if len(parts) < 4:
            continue
        rows.append((parts[0], parts[1], parts[2], parts[3]))
    return rows


def full_hash(cwd: str, short: str) -> str:
    # ^{commit} avoids ambiguous tag vs commit and matches commit objects only.
    return git_out(cwd, "rev-parse", "--verify", f"{short}^{{commit}}").strip()


@dataclass
class Row:
    h: str
    date: str
    author: str
    message: str


# Display width (characters) used before the message column (hash, date, author, gaps).
_MSG_COL_OFFSET = 51


def _message_wrap_width(cols: int) -> int:
    return max(cols - _MSG_COL_OFFSET, 24)


def _wrap_visual_line(text: str, width: int) -> List[str]:
    t = text.rstrip("\n")
    if not t:
        return []
    out = textwrap.wrap(
        t,
        width=width,
        break_long_words=True,
        break_on_hyphens=True,
        replace_whitespace=False,
        drop_whitespace=False,
    )
    return out if out else [t]


def print_commit_block(row: Row, mode: str, body_lines_max: int, cols: int) -> None:
    msg = row.message
    if "\n" in msg:
        first, rest = msg.split("\n", 1)
    else:
        first, rest = msg, ""
    rest = strip_body_edges(rest)
    if not rest.strip():
        rest = ""

    mw = _message_wrap_width(cols)
    author = row.author[:20]
    head = (
        f"  {C.YELLOW}{row.h:<10}{C.RESET}  {C.DIM}{row.date:<11}{C.RESET}  "
        f"{C.GREEN}{author:<22}{C.RESET}  "
    )
    cont_conv = (
        f"  {C.YELLOW}{'':<10}{C.RESET}  {C.DIM}{'':<11}{C.RESET}  "
        f"{C.GREEN}{'':<22}{C.RESET}  "
    )
    cont_other = f"  {C.DIM}{'':<10}  {'':<11}  {'':<22}  "

    subj_chunks = _wrap_visual_line(first, mw) or [first]
    for i, chunk in enumerate(subj_chunks):
        if i == 0:
            sys.stdout.write(head)
            if mode == "conv":
                print(f"{format_conv_first_line(chunk)}{C.RESET}")
            else:
                print(f"{C.DIM}{chunk}{C.RESET}")
        else:
            if mode == "conv":
                print(f"{cont_conv}{C.DIM}{chunk}{C.RESET}")
            else:
                print(f"{cont_other}{C.DIM}{chunk}{C.RESET}")

    if not rest:
        return

    shown = 0
    total_nb = 0
    for line in rest.splitlines():
        if not line.strip():
            continue
        if RE_CHERRY_BODY.match(line):
            continue
        total_nb += 1
        if shown < body_lines_max:
            for part in _wrap_visual_line(line, mw):
                if mode == "conv":
                    print(f"{cont_conv}{C.DIM}{part}{C.RESET}")
                else:
                    print(f"{cont_other}{C.DIM}{part}{C.RESET}")
            shown += 1
    if total_nb > body_lines_max:
        if mode == "conv":
            print(
                f"  {C.YELLOW}{'':<10}{C.RESET}  {C.DIM}{'':<11}{C.RESET}  "
                f"{C.GREEN}{'':<22}{C.RESET}  {C.DIM}… (truncated){C.RESET}"
            )
        else:
            print(
                f"  {C.DIM}{'':<10}  {'':<11}  {'':<22}  … (truncated){C.RESET}"
            )


def check_repo(
    cwd: str,
    label: str,
    fullpath: str,
    main_b: str,
    release_b: str,
    remote: str,
    *,
    conventional_only: bool,
    feat_fix_only: bool,
    body_lines_max: int,
    subject_dedup: bool,
    patch_id_index_max: int,
    verbose: bool,
) -> None:
    fr = git_run(cwd, ["fetch", remote, "--quiet"])
    if fr.returncode != 0:
        report_git_failure(f"git fetch {remote!r}", cwd, fr)

    mb = resolve_ref(cwd, remote, main_b)
    rb = resolve_ref(cwd, remote, release_b)

    if not git_ok(cwd, "rev-parse", "--verify", mb):
        print(f"{C.DIM}  skipping {label} — branch {main_b} not found{C.RESET}")
        return
    if not git_ok(cwd, "rev-parse", "--verify", rb):
        print(f"{C.DIM}  skipping {label} — branch {release_b} not found{C.RESET}")
        return

    ported = collect_ported_blob(cwd, rb)
    rel_patch_subj, rel_norm_subj, n_rel = build_release_maps(
        cwd, rb, patch_id_index_max
    )
    if verbose:
        print(
            f"[{label}] release {rb}: indexed {n_rel} commit(s); "
            f"{len(rel_norm_subj)} normalized subject(s)",
            file=sys.stderr,
        )

    rs = "\x1e"
    conv: List[Row] = []
    other: List[Row] = []

    records = parse_git_log_records(cwd, mb, rb, rs)
    if verbose:
        print(
            f"[{label}] candidates (main not in release): {len(records)}",
            file=sys.stderr,
        )

    for gh, date, author, message in records:
        fh = full_hash(cwd, gh)
        if not fh:
            continue
        if ported and fh[:12] in ported:
            continue

        first_line = message.split("\n", 1)[0]

        cpid = stable_patch_id(cwd, gh)
        if cpid and cpid in rel_patch_subj:
            ct = normalize_subject_key(first_line)
            if ct and any(
                normalize_subject_key(s) == ct for s in rel_patch_subj[cpid]
            ):
                continue

        if subject_dedup:
            nk = normalize_subject_key(first_line)
            if nk and nk in rel_norm_subj:
                continue

        if is_conventional(first_line):
            if feat_fix_only and not is_feat_or_fix(first_line):
                continue
            conv.append(Row(gh, date, author, message))
        else:
            if feat_fix_only:
                continue
            other.append(Row(gh, date, author, message))

    if not conv and not other:
        if verbose and records:
            print(
                f"[{label}] all {len(records)} candidate(s) filtered out "
                f"(-x / patch-id+subject / SUBJECT_DEDUP / type filters)",
                file=sys.stderr,
            )
        return
    if conventional_only and not conv:
        if verbose and records:
            print(
                f"[{label}] no conventional commits to show "
                f"({len(records)} candidate(s) filtered)",
                file=sys.stderr,
            )
        return
    if feat_fix_only and not conv:
        if verbose and records:
            print(
                f"[{label}] no feat/fix commits to show "
                f"({len(records)} candidate(s) filtered)",
                file=sys.stderr,
            )
        return

    cols = int(os.environ.get("COLUMNS", "120"))
    if cols < 88:
        cols = 88
    rule = "─" * cols

    print()
    if label == "root":
        print(f"{C.BOLD}{C.CYAN}● root repo{C.RESET}")
    else:
        print(
            f"{C.BOLD}{C.CYAN}● submodule: {label}{C.RESET}  {C.DIM}({fullpath}){C.RESET}"
        )
    print(f"{C.DIM}{rule}{C.RESET}")
    print(
        f"{C.DIM}  {'hash':<10}  {'date':<11}  {'author':<22}  message{C.RESET}"
    )
    print(f"{C.DIM}{rule}{C.RESET}")

    if conv and other and not conventional_only:
        print()
        print(
            f"{C.BOLD}  Conventional commits{C.RESET}  {C.DIM}(feat, fix, docs, …){C.RESET}"
        )
        print(f"{C.DIM}{rule}{C.RESET}")

    for row in conv:
        print_commit_block(row, "conv", body_lines_max, cols)

    if other and not conventional_only:
        print()
        if conv:
            print(
                f"{C.DIM}  Other commits{C.RESET}  {C.DIM}(no conventional prefix; often minor){C.RESET}"
            )
        else:
            print(
                f"{C.DIM}  Other commits{C.RESET}  {C.DIM}(no feat/fix/docs/… prefix){C.RESET}"
            )
        print(f"{C.DIM}{rule}{C.RESET}")
        for row in other:
            print_commit_block(row, "other", body_lines_max, cols)


def repo_root_from_git() -> str:
    start = os.getcwd()
    here = git_out(start, "rev-parse", "--show-toplevel").strip()
    if not here:
        print("not a git repository", file=sys.stderr)
        sys.exit(1)
    super_w = git_out(
        start, "rev-parse", "--show-superproject-working-tree"
    ).strip()
    if super_w and os.path.isfile(os.path.join(super_w, ".gitmodules")):
        return super_w
    return here


def submodule_paths(repo_root: str) -> List[str]:
    cfg = os.path.join(repo_root, ".gitmodules")
    if not os.path.isfile(cfg):
        return []
    r = subprocess.run(
        ["git", "config", "--file", cfg, "--get-regexp", r"^submodule\..*\.path$"],
        capture_output=True,
        text=True,
    )
    paths: List[str] = []
    for line in (r.stdout or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) >= 2:
            paths.append(parts[1].strip().replace("\r", ""))
    return paths


def main() -> None:
    p = argparse.ArgumentParser(
        description="List commits on main not on release (superproject + submodules).",
    )
    p.add_argument("-c", "--conventional-only", action="store_true")
    p.add_argument("-F", "--feat-fix-only", action="store_true")
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print diagnostics on stderr (counts, filter outcomes)",
    )
    p.add_argument("main_branch", nargs="?", default="main")
    p.add_argument("release_branch", nargs="?", default="release/1.0")
    p.add_argument("remote", nargs="?", default="origin")
    args = p.parse_args()

    body_max = int(os.environ.get("BODY_LINES_MAX", "4"))
    subject_dedup = os.environ.get("SUBJECT_DEDUP", "1") != "0"
    patch_max = int(os.environ.get("PATCH_ID_INDEX_MAX", "0") or "0")

    repo_root = repo_root_from_git()
    try:
        os.chdir(repo_root)
    except OSError as e:
        print(f"cannot cd to repo root: {repo_root}: {e}", file=sys.stderr)
        sys.exit(1)

    print()
    print(
        f"{C.BOLD}Unported commits{C.RESET}  {C.CYAN}{args.main_branch}{C.RESET} "
        f"→ not in {C.CYAN}{args.release_branch}{C.RESET}"
    )
    print(
        f"{C.DIM}Excluded: -x trailers (win on conflict); same patch-id + subject on release; "
        f"or same normalized subject on release (SUBJECT_DEDUP=0 disables the last).{C.RESET}"
    )
    if args.conventional_only:
        print(
            f"{C.DIM}Showing conventional commits only (use without --conventional-only to include the rest).{C.RESET}"
        )
    if args.feat_fix_only:
        print(
            f"{C.DIM}Showing only feat: and fix: (use without --feat-fix-only to include other types).{C.RESET}"
        )

    check_repo(
        repo_root,
        "root",
        ".",
        args.main_branch,
        args.release_branch,
        args.remote,
        conventional_only=args.conventional_only,
        feat_fix_only=args.feat_fix_only,
        body_lines_max=body_max,
        subject_dedup=subject_dedup,
        patch_id_index_max=patch_max,
        verbose=args.verbose,
    )

    if os.path.isfile(os.path.join(repo_root, ".gitmodules")):
        for subpath in submodule_paths(repo_root):
            if not subpath:
                continue
            local_path = os.path.join(repo_root, subpath)
            if not git_ok(local_path, "rev-parse", "--is-inside-work-tree"):
                print(
                    f"{C.DIM}  skipping submodule {subpath} — not a git checkout{C.RESET}"
                )
                continue
            name = os.path.basename(subpath.rstrip("/"))
            check_repo(
                local_path,
                name,
                subpath,
                args.main_branch,
                args.release_branch,
                args.remote,
                conventional_only=args.conventional_only,
                feat_fix_only=args.feat_fix_only,
                body_lines_max=body_max,
                subject_dedup=subject_dedup,
                patch_id_index_max=patch_max,
                verbose=args.verbose,
            )

    print()


if __name__ == "__main__":
    main()
