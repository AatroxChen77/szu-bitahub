#!/usr/bin/env python3
"""Build and verify Zero-Tar immutable code release directories for SZU BitaHub.

Features:
  - Copies source tree (or explicit --include subpaths) into an immutable versioned directory
  - Strips .git, __pycache__, *.pyc, *.pyo, .DS_Store, and user-defined exclude patterns
  - Fails closed on symlinks by default (or handles them explicitly via --symlinks)
  - Writes deterministic CODE-MANIFEST.sha256 and verifies the staged tree immediately
  - Standalone --verify mode checks an already-staged or round-trip downloaded directory
"""
from __future__ import annotations

import argparse
from fnmatch import fnmatch
import hashlib
import os
from pathlib import Path
import shutil
import sys

DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git",
    ".git/**",
    "__pycache__",
    "*/__pycache__",
    "*/__pycache__/*",
    "__pycache__/*",
    ".pytest_cache",
    ".mypy_cache",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
)
MANIFEST_FILENAME = "CODE-MANIFEST.sha256"


def sha256_file(path: Path) -> str:
    """Return hex SHA-256 digest of a file without buffering entire content."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_excluded(rel_posix: str, name: str, excludes: tuple[str, ...]) -> bool:
    parts = rel_posix.split("/")
    for pat in excludes:
        if fnmatch(rel_posix, pat) or fnmatch(name, pat) or any(fnmatch(p, pat) for p in parts):
            return True
    return False


def _collect_source_files(
    src_root: Path,
    includes: list[str],
    excludes: tuple[str, ...],
    symlink_mode: str,
) -> list[tuple[Path, str]]:
    roots: list[Path] = []
    if includes:
        for rel in includes:
            rel_path = Path(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                raise ValueError(f"--include path must be relative inside --src without '..': {rel!r}")
            target = (src_root / rel_path).resolve() if symlink_mode == "dereference" else (src_root / rel_path)
            if not target.exists():
                raise FileNotFoundError(f"Requested --include path not found: {src_root / rel_path}")
            roots.append(src_root / rel_path)
    else:
        roots.append(src_root)

    collected: dict[str, Path] = {}
    follow_links = symlink_mode == "dereference"

    for entry in roots:
        if entry.is_symlink():
            if symlink_mode == "error":
                raise ValueError(f"Symlink forbidden in release input: {entry}")
            if symlink_mode == "skip":
                continue
        if entry.is_file():
            rel = entry.relative_to(src_root).as_posix()
            if not _is_excluded(rel, entry.name, excludes):
                collected[rel] = entry.resolve() if follow_links else entry
            continue

        for dirpath, dirnames, filenames in os.walk(entry, followlinks=follow_links):
            dp = Path(dirpath)
            rel_dir = dp.relative_to(src_root).as_posix()
            kept_dirs: list[str] = []
            for d in sorted(dirnames):
                child = dp / d
                child_rel = d if rel_dir == "." else f"{rel_dir}/{d}"
                if _is_excluded(child_rel, d, excludes):
                    continue
                if child.is_symlink():
                    if symlink_mode == "error":
                        raise ValueError(f"Symlink directory forbidden in release input: {child}")
                    if symlink_mode == "skip":
                        continue
                kept_dirs.append(d)
            dirnames[:] = kept_dirs

            for fn in sorted(filenames):
                fp = dp / fn
                rel = fn if rel_dir == "." else f"{rel_dir}/{fn}"
                if _is_excluded(rel, fn, excludes) or rel == MANIFEST_FILENAME:
                    continue
                if fp.is_symlink():
                    if symlink_mode == "error":
                        raise ValueError(f"Symlink file forbidden in release input: {fp}")
                    if symlink_mode == "skip":
                        continue
                collected[rel] = fp.resolve(strict=True) if follow_links else fp

    return [(collected[k], k) for k in sorted(collected)]


def _validate_manifest_rel_path(rel: str, mpath: Path, lineno: int) -> None:
    if not rel or rel.startswith("/") or "\\" in rel:
        raise ValueError(f"Unsafe relative path on line {lineno} in {mpath}: {rel!r}")
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Invalid path segment on line {lineno} in {mpath}: {rel!r}")


def verify_release_dir(release_dir: Path, manifest_path: Path | None = None) -> int:
    """Fail-closed verification of file set, zero symlinks, and SHA-256 digests."""
    if not release_dir.is_dir():
        raise NotADirectoryError(f"Release directory does not exist: {release_dir}")
    mpath = manifest_path or (release_dir / MANIFEST_FILENAME)
    if not mpath.is_file():
        raise FileNotFoundError(f"Missing manifest file: {mpath}")

    expected: dict[str, str] = {}
    for lineno, raw in enumerate(mpath.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if "  " not in line:
            raise ValueError(f"Invalid sha256sum line {lineno} in {mpath}: {raw!r}")
        digest, rel = line.split("  ", 1)
        if len(digest) != 64:
            raise ValueError(f"Invalid SHA-256 hex digest on line {lineno} in {mpath}")
        _validate_manifest_rel_path(rel, mpath, lineno)
        if rel in expected:
            raise ValueError(f"Duplicate manifest entry on line {lineno} in {mpath}: {rel!r}")
        expected[rel] = digest

    actual: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(release_dir, followlinks=False):
        dp = Path(dirpath)
        for d in dirnames:
            sub = dp / d
            if sub.is_symlink():
                raise ValueError(f"Symlink directory forbidden in release: {sub.relative_to(release_dir)}")
        for fn in filenames:
            fp = dp / fn
            if fp.is_symlink():
                raise ValueError(f"Symlink file forbidden in release: {fp.relative_to(release_dir)}")
            rel = fp.relative_to(release_dir).as_posix()
            if fp.resolve() == mpath.resolve():
                continue
            actual.add(rel)

    missing = sorted(set(expected) - actual)
    extra = sorted(actual - set(expected))
    if missing or extra:
        raise ValueError(
            f"Release file set mismatch in {release_dir}: missing={missing[:5]} (n={len(missing)}), extra={extra[:5]} (n={len(extra)})"
        )

    bad_hash: list[str] = []
    for rel, exp_sha in sorted(expected.items()):
        got = sha256_file(release_dir / rel)
        if got != exp_sha:
            bad_hash.append(rel)
    if bad_hash:
        raise ValueError(f"SHA-256 mismatch on {len(bad_hash)} files in {release_dir}: {bad_hash[:5]}")

    print(f"[CODE-MANIFEST PASS] {release_dir} ({len(expected)} files verified)")
    return len(expected)


def stage_release(
    src_root: Path,
    out_dir: Path,
    name: str,
    *,
    includes: list[str],
    excludes: tuple[str, ...],
    symlink_mode: str,
    force: bool,
    dry_run: bool,
) -> Path:
    """Stage immutable release directory with CODE-MANIFEST.sha256."""
    if not src_root.is_dir():
        raise NotADirectoryError(f"Source directory not found: {src_root}")
    if "/" in name or "\\" in name or name in ("", ".", ".."):
        raise ValueError(f"Invalid release directory name: {name!r}")

    src_res = src_root.resolve()
    out_res = out_dir.resolve()
    if out_res == src_res:
        raise ValueError("--out-dir cannot be identical to --src")
    if out_res.is_relative_to(src_res):
        out_rel = out_res.relative_to(src_res).as_posix()
        excludes = excludes + (out_rel, f"{out_rel}/*")

    release_dir = out_res / name
    if release_dir.exists() and not force and not dry_run:
        raise FileExistsError(
            f"Target release directory already exists: {release_dir}. "
            "Use a new immutable version name (recommended) or pass --force."
        )

    files = _collect_source_files(src_res, includes, excludes, symlink_mode)
    if not files:
        raise RuntimeError(f"No files matched for staging under {src_root}")

    if dry_run:
        print(f"[DRY-RUN] Would stage {len(files)} files into {release_dir}")
        for _, rel in files[:20]:
            print(f"  + {rel}")
        if len(files) > 20:
            print(f"  ... ({len(files) - 20} more files)")
        return release_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir.resolve() / f".staging-{name}-{os.getpid()}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        manifest_lines: list[str] = []
        for src_file, rel in files:
            dst = tmp_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst)
            manifest_lines.append(f"{sha256_file(dst)}  {rel}")

        manifest_lines.sort(key=lambda s: s.split("  ", 1)[1])
        (tmp_dir / MANIFEST_FILENAME).write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

        if release_dir.exists():
            shutil.rmtree(release_dir)
        tmp_dir.rename(release_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    verified_count = verify_release_dir(release_dir)
    print(f"[STAGED] {release_dir} ({verified_count} files) -> {release_dir / MANIFEST_FILENAME}")
    return release_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage or verify a Zero-Tar immutable code release directory.")
    parser.add_argument("--src", type=Path, help="Local source repository/directory to stage.")
    parser.add_argument("--out-dir", type=Path, help="Parent directory where the versioned release folder is created.")
    parser.add_argument("--name", help="Versioned folder name (e.g., code-v1).")
    parser.add_argument("--include", action="append", default=[], help="Optional relative subpath under --src (repeatable).")
    parser.add_argument("--exclude", action="append", default=[], help="Additional glob pattern to exclude (repeatable).")
    parser.add_argument(
        "--symlinks",
        choices=("error", "skip", "dereference"),
        default="error",
        help="How to handle symlinks in --src (default: error).",
    )
    parser.add_argument("--force", action="store_true", help="Allow overwriting an existing local staging release directory.")
    parser.add_argument("--dry-run", action="store_true", help="List files that would be staged without copying.")
    parser.add_argument("--verify", type=Path, help="Verify an existing release directory against CODE-MANIFEST.sha256.")
    parser.add_argument("--manifest", type=Path, help="Explicit manifest path when using --verify.")
    args = parser.parse_args()

    if args.verify:
        verify_release_dir(args.verify, args.manifest)
        return 0

    if not (args.src and args.out_dir and args.name):
        parser.error("Staging requires --src, --out-dir, and --name (or pass --verify <dir>).")

    excludes = DEFAULT_EXCLUDES + tuple(args.exclude)
    stage_release(
        args.src,
        args.out_dir,
        args.name,
        includes=args.include,
        excludes=excludes,
        symlink_mode=args.symlinks,
        force=args.force,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
