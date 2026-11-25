#!/usr/bin/env python3
"""Render and validate a `bita apply -f` training YAML from a Web-UI-exported template.

Zero tenant/account IDs are hardcoded: clusterId, teamId, resourcePoolId,
resourcePoolConfId, imageId, and storage mounts are preserved from --template
unless explicitly overridden by CLI flags.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import os
from pathlib import Path
import re
from typing import Any

try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    yaml = None
    _YAML_IMPORT_ERR = exc
else:
    _YAML_IMPORT_ERR = None

FORBIDDEN_NAME_CHARS = set('\\/:*?"<>|')
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def _require_yaml() -> Any:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required for render_bitahub_yaml.py (`pip install pyyaml` or run with your conda env Python). "
            f"Import error: {_YAML_IMPORT_ERR}"
        )
    return yaml


def validate_job_name(name: str) -> None:
    """Check official job-name length and character constraints (<=128 chars, no spaces or special symbols)."""
    if not name or len(name) > 128:
        raise ValueError(f"Job name must be 1..128 characters (got len={len(name)}): {name!r}")
    if any(ch.isspace() or ch in FORBIDDEN_NAME_CHARS for ch in name) or not NAME_PATTERN.match(name):
        raise ValueError(f"Job name contains unsupported characters: {name!r}")


def validate_bitahub_command(cmd: str, *, allow_tar: bool = False) -> None:
    """Enforce single-line /bin/sh startup rules (no newlines, backslash continuations, source, or startup tar)."""
    if not cmd or not cmd.strip():
        raise ValueError("Startup command cannot be empty")
    if "\n" in cmd or "\r" in cmd:
        raise ValueError(f"Startup command must be single-line for BitaHub /bin/sh: {cmd!r}")
    if "\\" in cmd:
        raise ValueError(f"Startup command must not use '\\' line continuations: {cmd!r}")
    stripped = cmd.lstrip()
    if stripped.startswith(".") and (len(stripped) == 1 or stripped[1].isspace()):
        raise ValueError(f"Startup command cannot use '.' sourcing under /bin/sh: {cmd!r}")
    if re.search(r"(^|[;&|\s])source(\s|$)", cmd):
        raise ValueError(f"BitaHub /bin/sh rejects `source`; invoke `bash /path/to/script.sh` instead: {cmd!r}")
    if not allow_tar and (
        re.search(r"(^|[;&|\s])tar(\s|$)", cmd) or re.search(r"\.tar(\.[A-Za-z0-9]+|\b)", cmd)
    ):
        raise ValueError(
            f"Zero-Tar policy rejects tar extraction in startup command ({cmd!r}); pass --allow-tar only if intentional."
        )


def _update_first_matching_key(obj: Any, candidate_keys: tuple[str, ...], value: Any) -> bool:
    if isinstance(obj, dict):
        for k in candidate_keys:
            if k in obj and not isinstance(obj[k], (dict, list)):
                obj[k] = value
                return True
        for v in obj.values():
            if _update_first_matching_key(v, candidate_keys, value):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if _update_first_matching_key(item, candidate_keys, value):
                return True
    return False


def _merge_env_entries(spec: dict[str, Any], updates: dict[str, str]) -> bool:
    if not updates:
        return True

    def _walk(node: Any) -> bool:
        if isinstance(node, dict):
            for k in ("envs", "env", "environments"):
                if k in node:
                    cur = node[k]
                    if isinstance(cur, dict):
                        cur.update(updates)
                        return True
                    if isinstance(cur, list):
                        key_field = "name" if (cur and isinstance(cur[0], dict) and "name" in cur[0]) else "key"
                        by_k = {item.get(key_field): item for item in cur if isinstance(item, dict) and key_field in item}
                        for ek, ev in updates.items():
                            if ek in by_k:
                                by_k[ek]["value"] = ev
                            else:
                                cur.append({key_field: ek, "value": ev})
                        return True
            for v in node.values():
                if _walk(v):
                    return True
        elif isinstance(node, list):
            for item in node:
                if _walk(item):
                    return True
        return False

    if _walk(spec):
        return True
    return False


def validate_existing_doc(doc: dict[str, Any], *, allow_tar: bool = False) -> None:
    """Validate structural and startup-command safety on an existing BitaHub job YAML dict."""
    if not isinstance(doc, dict):
        raise ValueError("YAML root must be a mapping")
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("YAML missing `spec` mapping")
    job_kind = spec.get("jobKind") if "jobKind" in spec else doc.get("jobKind")
    if job_kind != "training":
        raise ValueError(f"Expected `jobKind: training`, got {job_kind!r}")

    meta = doc.get("metadata")
    name = meta.get("name") if isinstance(meta, dict) else spec.get("name")
    if isinstance(name, str):
        validate_job_name(name)

    tasks = spec.get("tasks")
    cmd = None
    if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict):
        cmd = tasks[0].get("command")
        task_num = tasks[0].get("taskNum")
        if task_num is not None and int(task_num) != 1:
            raise ValueError(f"This skill only validates single-replica (`taskNum: 1`) jobs; got taskNum={task_num!r}")
    elif isinstance(spec.get("command"), str):
        cmd = spec["command"]

    if not isinstance(cmd, str):
        raise ValueError("Missing string command at `spec.tasks[0].command` or `spec.command`")
    validate_bitahub_command(cmd, allow_tar=allow_tar)


def render_yaml_doc(
    base_doc: dict[str, Any],
    *,
    name: str,
    command: str,
    image: str | None = None,
    image_id: str | None = None,
    flavor_conf_id: str | None = None,
    gpus: int | None = None,
    mount_path: str | None = None,
    env_updates: dict[str, str] | None = None,
    heartbeat_minutes: int | None = 30,
    auto_retry: int | None = 0,
    retry_max: int | None = None,
    retry_interval: int | None = None,
    allow_tar: bool = False,
) -> dict[str, Any]:
    """Return a stamped copy of a Web-exported BitaHub training YAML."""
    validate_job_name(name)
    validate_bitahub_command(command, allow_tar=allow_tar)
    if heartbeat_minutes is not None and int(heartbeat_minutes) < 0:
        raise ValueError(f"--heartbeat-minutes must be >= 0 (got {heartbeat_minutes!r})")

    doc = deepcopy(base_doc)
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("Template YAML missing `spec` mapping")
    job_kind = spec.get("jobKind") if "jobKind" in spec else doc.get("jobKind")
    if job_kind != "training":
        raise ValueError(f"Template YAML must use `jobKind: training`, got {job_kind!r}")

    meta = doc.get("metadata")
    if isinstance(meta, dict) and "name" in meta:
        meta["name"] = name
    elif "name" in spec:
        spec["name"] = name
    else:
        doc["metadata"] = {"name": name}

    tasks = spec.get("tasks")
    if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict):
        tasks[0]["command"] = command
        if flavor_conf_id:
            tasks[0]["resourcePoolConfId"] = flavor_conf_id
    else:
        if not _update_first_matching_key(spec, ("command", "startupCommand", "startCommand", "cmd"), command):
            raise ValueError("Could not locate command field in template YAML")
        if flavor_conf_id:
            _update_first_matching_key(spec, ("resourcePoolConfId", "flavor", "resourceSpec", "flavorId"), flavor_conf_id)

    if image:
        _update_first_matching_key(spec, ("image", "imageUrl", "imageName"), image)
    if image_id:
        spec["imageId"] = image_id
    if gpus is not None:
        _update_first_matching_key(spec, ("gpuNum", "gpuCount", "gpus"), int(gpus))
    if mount_path:
        inputs = spec.get("storage", {}).get("input") if isinstance(spec.get("storage"), dict) else None
        if not isinstance(inputs, list) or not inputs or not isinstance(inputs[0], dict):
            raise ValueError("Template missing `spec.storage.input[0]` for --mount-path override")
        inputs[0]["mountPath"] = mount_path

    if env_updates:
        merged = _merge_env_entries(spec, env_updates)
        if not merged:
            raise ValueError(
                "Template YAML does not contain an existing `env`/`envs`/`environments` schema; "
                "export environment variables inside your `.sh` entrypoint script instead."
            )

    ft = spec.setdefault("faultTolerance", {})
    if isinstance(ft, dict):
        if heartbeat_minutes is not None:
            ft["suspendDetection"] = 1 if heartbeat_minutes > 0 else 0
            ft["suspendDetectionDuration"] = int(heartbeat_minutes)
        if auto_retry is not None:
            ft["autoRetry"] = int(auto_retry)
            if int(auto_retry) == 0:
                ft.setdefault("retryConditions", [])
                ft["retryMaxNum"] = 0
                ft["retryInterval"] = 0
        if retry_max is not None:
            ft["retryMaxNum"] = int(retry_max)
        if retry_interval is not None:
            ft["retryInterval"] = int(retry_interval)

    validate_existing_doc(doc, allow_tar=allow_tar)
    return doc


def _parse_envs(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in items:
        if "=" not in raw:
            raise ValueError(f"--env must use KEY=VALUE format (got {raw!r})")
        k, v = raw.split("=", 1)
        out[k.strip()] = v
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Render or validate SZU BitaHub training YAML.")
    parser.add_argument("--template", type=Path, help="Web-exported BitaHub training YAML template.")
    parser.add_argument("--name", help="Job name (<=128 chars, alphanumeric/._-).")
    parser.add_argument("--command", help="Single-line /bin/sh startup command (e.g. 'bash /mnt/.../run.sh').")
    parser.add_argument("--out", type=Path, help="Output YAML path.")
    parser.add_argument("--validate-only", type=Path, help="Validate an existing BitaHub YAML without writing.")
    parser.add_argument("--image", help="Optional override for image URL field if present.")
    parser.add_argument("--image-id", help="Optional override for `spec.imageId`.")
    parser.add_argument("--flavor-conf-id", help="Optional override for `resourcePoolConfId` (e.g. 2-GPU SKU).")
    parser.add_argument("--gpus", type=int, help="Optional GPU count update if field exists in template.")
    parser.add_argument("--mount-path", help="Optional override for `spec.storage.input[0].mountPath`.")
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        help="Extra KEY=VALUE entry if template already defines an env schema (otherwise export inside run.sh).",
    )
    parser.add_argument("--heartbeat-minutes", type=int, default=30, help="Hang detection window in minutes (default: 30).")
    parser.add_argument("--auto-retry", type=int, choices=(0, 1), default=0, help="Auto-retry flag (default: 0).")
    parser.add_argument("--retry-max", type=int, help="Optional retryMaxNum override.")
    parser.add_argument("--retry-interval", type=int, help="Optional retryInterval override in seconds.")
    parser.add_argument("--allow-tar", action="store_true", help="Allow `tar` in startup command (not recommended).")
    args = parser.parse_args()

    y = _require_yaml()

    if args.validate_only:
        doc = y.safe_load(args.validate_only.read_text(encoding="utf-8"))
        validate_existing_doc(doc, allow_tar=args.allow_tar)
        print(f"[YAML PASS] {args.validate_only}")
        return 0

    if not (args.template and args.name and args.command and args.out):
        parser.error("Rendering requires --template, --name, --command, and --out (or pass --validate-only <file>).")

    base_doc = y.safe_load(args.template.read_text(encoding="utf-8"))
    env_updates = _parse_envs(args.env)
    doc = render_yaml_doc(
        base_doc,
        name=args.name,
        command=args.command,
        image=args.image,
        image_id=args.image_id,
        flavor_conf_id=args.flavor_conf_id,
        gpus=args.gpus,
        mount_path=args.mount_path,
        env_updates=env_updates,
        heartbeat_minutes=args.heartbeat_minutes,
        auto_retry=args.auto_retry,
        retry_max=args.retry_max,
        retry_interval=args.retry_interval,
        allow_tar=args.allow_tar,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = args.out.with_name(f".{args.out.name}.partial.{os.getpid()}")
    rendered = "# Rendered by szu-bitahub render_bitahub_yaml.py\n" + y.safe_dump(
        doc, sort_keys=False, allow_unicode=True
    )
    tmp_out.write_text(rendered, encoding="utf-8")
    os.replace(tmp_out, args.out)
    print(f"[YAML WRITTEN] {args.out} (name={args.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
