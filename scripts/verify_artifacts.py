#!/usr/bin/env python3
"""Generate and verify SHA-256 artifact manifests for SZU BitaHub runs (stdlib only).

Subcommands:
  generate --root <dir> [--manifest ARTIFACTS.sha256] [--include <glob>]... [--exclude <glob>]...
  verify   --root <dir> --manifest <manifest> [--allow-extra] [--allow-empty] [--min-files N]
"""
from __future__ import annotations

import argparse
from fnmatch import fnmatch
import hashlib
import os
from pathlib import Path

DEFAULT_EXCLUDES: tuple[str, ...] = ("*.partial", "*.tmp", ".DS_Store")


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest in 1 MiB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _matches_any(rel_posix: str, name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(rel_posix, p) or fnmatch(name, p) for p in patterns)


def generate_manifest(
    root: Path,
    manifest_path: Path,
    includes: tuple[str, ...],
    excludes: tuple[str, ...],
) -> int:
    """Walk root and write deterministic `<sha256>  <relpath>` manifest."""
    if not root.is_dir():
        raise NotADirectoryError(f"Root directory not found: {root}")
    root_res = root.resolve()
    manifest_res = manifest_path.resolve()

    lines: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_res, followlinks=False):
        dp = Path(dirpath)
        rel_dir = dp.relative_to(root_res).as_posix()
        kept_dirs: list[str] = []
        for d in sorted(dirnames):
            sub = dp / d
            d_rel = d if rel_dir == "." else f"{rel_dir}/{d}"
            if _matches_any(d_rel, d, excludes):
                continue
            if sub.is_symlink():
                raise ValueError(f"Symlink directory forbidden while generating artifact manifest: {sub}")
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for fn in sorted(filenames):
            fp = dp / fn
            if not fp.is_symlink() and fp.resolve() == manifest_res:
                continue
            rel = fp.relative_to(root_res).as_posix()
            if _matches_any(rel, fn, excludes):
                continue
            if includes and not _matches_any(rel, fn, includes):
                continue
            if fp.is_symlink():
                raise ValueError(f"Symlink forbidden while generating artifact manifest: {fp}")
            lines.append(f"{sha256_file(fp)}  {rel}")

    if not lines:
        raise RuntimeError(f"No files matched under {root_res}")
    lines.sort(key=lambda s: s.split("  ", 1)[1])

    manifest_res.parent.mkdir(parents=True, exist_ok=True)
    tmp_m = manifest_res.with_name(f".{manifest_res.name}.partial.{os.getpid()}")
    tmp_m.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp_m, manifest_res)
    print(f"[MANIFEST GENERATED] {manifest_res} ({len(lines)} files)")
    return len(lines)


def _validate_manifest_rel_path(rel: str, manifest_res: Path, lineno: int) -> None:
    if not rel or rel.startswith("/") or "\\" in rel:
        raise ValueError(f"Unsafe relative path on line {lineno} in {manifest_res}: {rel!r}")
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Invalid path segment on line {lineno} in {manifest_res}: {rel!r}")


def verify_manifest(
    root: Path,
    manifest_path: Path,
    *,
    allow_extra: bool,
    allow_empty: bool,
    min_files: int,
) -> int:
    """Verify artifacts under root match manifest_path cleanly."""
    if not root.is_dir():
        raise NotADirectoryError(f"Root directory not found: {root}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest file not found: {manifest_path}")

    root_res = root.resolve()
    manifest_res = manifest_path.resolve()

    expected: dict[str, str] = {}
    for lineno, raw in enumerate(manifest_res.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if "  " not in line:
            raise ValueError(f"Invalid manifest line {lineno} in {manifest_res}: {raw!r}")
        digest, rel = line.split("  ", 1)
        if len(digest) != 64:
            raise ValueError(f"Invalid SHA-256 digest on line {lineno} in {manifest_res}")
        _validate_manifest_rel_path(rel, manifest_res, lineno)
        if rel in expected:
            raise ValueError(f"Duplicate manifest entry on line {lineno} in {manifest_res}: {rel!r}")
        expected[rel] = digest

    if len(expected) < min_files:
        raise ValueError(f"Manifest contains {len(expected)} files, fewer than --min-files={min_files}")

    missing: list[str] = []
    empty_files: list[str] = []
    mismatched: list[str] = []
    symlinks: list[str] = []

    for rel, exp_sha in sorted(expected.items()):
        fp = root_res / rel
        if fp.is_symlink():
            symlinks.append(rel)
            continue
        if not fp.is_file():
            missing.append(rel)
            continue
        if not allow_empty and fp.stat().st_size == 0:
            empty_files.append(rel)
            continue
        if sha256_file(fp) != exp_sha:
            mismatched.append(rel)

    extra: list[str] = []
    if not allow_extra:
        for dirpath, dirnames, filenames in os.walk(root_res, followlinks=False):
            dp = Path(dirpath)
            for d in dirnames:
                sub = dp / d
                if sub.is_symlink():
                    symlinks.append(sub.relative_to(root_res).as_posix())
            for fn in filenames:
                fp = dp / fn
                if fp.is_symlink():
                    s_rel = fp.relative_to(root_res).as_posix()
                    if s_rel not in symlinks:
                        symlinks.append(s_rel)
                    continue
                if fp.resolve() == manifest_res:
                    continue
                rel = fp.relative_to(root_res).as_posix()
                if rel not in expected:
                    extra.append(rel)

    errors: list[str] = []
    if symlinks:
        errors.append(f"symlinks={symlinks[:5]} (n={len(symlinks)})")
    if missing:
        errors.append(f"missing={missing[:5]} (n={len(missing)})")
    if empty_files:
        errors.append(f"empty={empty_files[:5]} (n={len(empty_files)})")
    if mismatched:
        errors.append(f"sha256_mismatch={mismatched[:5]} (n={len(mismatched)})")
    if extra:
        errors.append(f"extra={extra[:5]} (n={len(extra)})")

    if errors:
        raise ValueError(f"Artifact verification failed in {root_res}: " + "; ".join(errors))

    print(f"[ARTIFACTS PASS] {root_res} ({len(expected)} files SHA-256 verified, allow_extra={allow_extra})")
    return len(expected)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify SHA-256 artifact manifests.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser("generate", help="Create SHA-256 manifest for a directory.")
    p_gen.add_argument("--root", type=Path, required=True, help="Artifact root directory.")
    p_gen.add_argument("--manifest", type=Path, help="Output manifest file (default: <root>/ARTIFACTS.sha256).")
    p_gen.add_argument("--include", action="append", default=[], help="Glob pattern to include (repeatable).")
    p_gen.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable).")

    p_ver = sub.add_parser("verify", help="Verify downloaded artifacts against a manifest.")
    p_ver.add_argument("--root", type=Path, required=True, help="Artifact root directory.")
    p_ver.add_argument("--manifest", type=Path, required=True, help="SHA-256 manifest file.")
    p_ver.add_argument("--allow-extra", action="store_true", help="Allow untracked extra files under --root.")
    p_ver.add_argument("--allow-empty", action="store_true", help="Allow 0-byte files if listed in manifest.")
    p_ver.add_argument("--min-files", type=int, default=1, help="Minimum required file count (default: 1).")

    args = parser.parse_args()
    if args.cmd == "generate":
        mpath = args.manifest or (args.root / "ARTIFACTS.sha256")
        generate_manifest(
            args.root,
            mpath,
            tuple(args.include),
            DEFAULT_EXCLUDES + tuple(args.exclude),
        )
        return 0

    verify_manifest(
        args.root,
        args.manifest,
        allow_extra=args.allow_extra,
        allow_empty=args.allow_empty,
        min_files=args.min_files,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
