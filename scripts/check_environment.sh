#!/usr/bin/env bash
# Generic pre-flight & in-container runtime health checker for SZU BitaHub jobs.
# Checks Python/PyTorch/CUDA, GPU visibility, persistent mount writability,
# short local TMPDIR (AF_UNIX <= 107B protection), offline env vars, and optional CODE-MANIFEST.
set -euo pipefail

MODE="auto"
PYTHON_BIN=""
MOUNT_DIR=""
CODE_DIR=""
EXPECT_GPUS=0
REQUIRE_GPU=0
CHECK_TMPDIR=""
STRICT=0

usage() {
  cat <<'EOF'
Usage:
  check_environment.sh [options]

Options:
  --mode <auto|local|container>   Check context label (default: auto)
  --python <path>                 Explicit Python interpreter path
  --mount <dir>                   Persistent mount directory to test read/write
  --code-dir <dir>                Immutable code directory containing CODE-MANIFEST.sha256
  --expect-gpus <N>               Expected visible GPU count via nvidia-smi / torch (0 = skip count assert)
  --require-gpu                   Fail if CUDA is unavailable inside Python
  --tmpdir <dir>                  Directory to inspect as TMPDIR (defaults to $TMPDIR or /tmp)
  --strict                        Return non-zero on warnings as well as hard failures
  -h, --help                      Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --mount) MOUNT_DIR="$2"; shift 2 ;;
    --code-dir) CODE_DIR="$2"; shift 2 ;;
    --expect-gpus) EXPECT_GPUS="$2"; shift 2 ;;
    --require-gpu) REQUIRE_GPU=1; shift ;;
    --tmpdir) CHECK_TMPDIR="$2"; shift 2 ;;
    --strict) STRICT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[FAIL] Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "${MODE}" != "auto" && "${MODE}" != "local" && "${MODE}" != "container" ]]; then
  echo "[FAIL] --mode must be 'auto', 'local', or 'container' (got '${MODE}')" >&2
  exit 2
fi
if [[ ! "${EXPECT_GPUS}" =~ ^[0-9]+$ ]]; then
  echo "[FAIL] --expect-gpus must be a non-negative integer (got '${EXPECT_GPUS}')" >&2
  exit 2
fi

FAILS=0
WARNS=0

record_fail() { echo "[FAIL] $*" >&2; FAILS=$((FAILS + 1)); }
record_warn() { echo "[WARN] $*"; WARNS=$((WARNS + 1)); }
record_ok()   { echo "[OK] $*"; }

echo "=== SZU BitaHub Environment Check (mode=${MODE}) ==="
echo "Host=$(hostname) UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 1. Resolve Python interpreter without hardcoding user project paths
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
    PYTHON_BIN="${CONDA_PREFIX}/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  fi
fi

if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
  record_fail "No executable Python interpreter found (pass --python /path/to/python)."
else
  record_ok "Python=${PYTHON_BIN} ($("${PYTHON_BIN}" --version 2>&1))"
fi

# 2. Check TMPDIR length against Linux AF_UNIX sun_path 107-byte limit
EFFECTIVE_TMP="${CHECK_TMPDIR:-${TMPDIR:-/tmp}}"
TMP_LEN=${#EFFECTIVE_TMP}
echo "Effective TMPDIR=${EFFECTIVE_TMP} (len=${TMP_LEN})"
if (( TMP_LEN > 48 )); then
  record_warn "TMPDIR length (${TMP_LEN}) > 48 chars; PyTorch multiprocessing appends ~32B and risks 'OSError: AF_UNIX path too long' (>107B). Use a short local path such as /tmp/bt_tmp."
else
  record_ok "TMPDIR length (${TMP_LEN}) is safely within AF_UNIX budget."
fi
if [[ -n "${MOUNT_DIR}" && "${EFFECTIVE_TMP}" == "${MOUNT_DIR%/}"* ]]; then
  record_warn "TMPDIR sits inside persistent mount (${MOUNT_DIR}); network filesystems may ignore chmod 0700. Prefer container-local /tmp."
fi
if [[ ! -d "${EFFECTIVE_TMP}" || ! -w "${EFFECTIVE_TMP}" ]]; then
  record_fail "TMPDIR does not exist or is not writable: ${EFFECTIVE_TMP}"
fi

# 3. Persistent mount read/write probe (only if --mount specified)
if [[ -n "${MOUNT_DIR}" ]]; then
  if [[ ! -d "${MOUNT_DIR}" ]]; then
    record_fail "Mount directory missing: ${MOUNT_DIR}"
  elif [[ ! -w "${MOUNT_DIR}" ]]; then
    record_fail "Mount directory not writable: ${MOUNT_DIR}"
  else
    PROBE_FILE="$(mktemp "${MOUNT_DIR%/}/.write_probe.XXXXXX" 2>/dev/null || true)"
    if [[ -n "${PROBE_FILE}" && -f "${PROBE_FILE}" ]]; then
      echo "probe" > "${PROBE_FILE}"
      rm -f "${PROBE_FILE}"
      record_ok "Persistent mount writable: ${MOUNT_DIR}"
    else
      record_fail "Failed atomic write probe in mount directory: ${MOUNT_DIR}"
    fi
  fi
fi

# 4. Offline & unbuffered flags advisory
for env_name in HF_HUB_OFFLINE TRANSFORMERS_OFFLINE PYTHONNOUSERSITE PYTHONUNBUFFERED; do
  val="${!env_name:-}"
  if [[ -z "${val}" ]]; then
    if [[ "${MODE}" == "local" ]]; then
      echo "[INFO] Compute-node env var unset locally (set in run.sh): ${env_name}=1"
    else
      record_warn "Recommended compute-node env var unset: ${env_name}=1"
    fi
  else
    record_ok "${env_name}=${val}"
  fi
done

# 5. GPU / PyTorch CUDA visibility
if command -v nvidia-smi >/dev/null 2>&1; then
  GPU_LIST="$(nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader 2>/dev/null || true)"
  if [[ -n "${GPU_LIST}" ]]; then
    record_ok "nvidia-smi detected GPUs:"
    while IFS= read -r line; do
      echo "     ${line}"
    done <<<"${GPU_LIST}"
    GPU_LINES="$(wc -l <<<"${GPU_LIST}" | tr -d ' ')"
    if (( EXPECT_GPUS > 0 && GPU_LINES != EXPECT_GPUS )); then
      record_fail "Expected ${EXPECT_GPUS} GPUs via nvidia-smi, observed ${GPU_LINES}."
    fi
  else
    if (( REQUIRE_GPU == 1 || EXPECT_GPUS > 0 )); then
      record_fail "nvidia-smi returned no GPUs."
    else
      record_warn "nvidia-smi present but no GPUs listed (CPU environment?)."
    fi
  fi
else
  if (( REQUIRE_GPU == 1 || EXPECT_GPUS > 0 )); then
    record_fail "nvidia-smi command not found while GPU is required."
  else
    echo "[INFO] nvidia-smi not found (normal on CPU login/workstation)."
  fi
fi

if [[ -n "${PYTHON_BIN}" && -x "${PYTHON_BIN}" ]]; then
  TORCH_STATUS="$("${PYTHON_BIN}" - <<'PYEOF' 2>&1 || true
try:
    import torch
    avail = torch.cuda.is_available()
    cnt = torch.cuda.device_count() if avail else 0
    names = [torch.cuda.get_device_name(i) for i in range(cnt)] if avail else []
    print(f"TORCH={torch.__version__} CUDA_AVAIL={avail} COUNT={cnt} NAMES={names}")
except Exception as exc:
    print(f"TORCH_ERR={type(exc).__name__}: {exc}")
PYEOF
)"
  echo "[INFO] ${TORCH_STATUS}"
  if [[ "${TORCH_STATUS}" == TORCH_ERR=* ]]; then
    if (( REQUIRE_GPU == 1 || EXPECT_GPUS > 0 )); then
      record_fail "Failed to import torch (${TORCH_STATUS})."
    else
      record_warn "torch import issue (${TORCH_STATUS})."
    fi
  elif (( REQUIRE_GPU == 1 )) && [[ "${TORCH_STATUS}" != *"CUDA_AVAIL=True"* ]]; then
    record_fail "PyTorch CUDA is not available (${TORCH_STATUS})."
  elif (( EXPECT_GPUS > 0 )); then
    if [[ "${TORCH_STATUS}" != *"CUDA_AVAIL=True"* ]]; then
      record_fail "PyTorch CUDA is not available while --expect-gpus=${EXPECT_GPUS} (${TORCH_STATUS})."
    elif [[ "${TORCH_STATUS}" =~ COUNT=([0-9]+) ]] && (( BASH_REMATCH[1] != EXPECT_GPUS )); then
      record_fail "Expected ${EXPECT_GPUS} CUDA devices inside PyTorch, observed ${BASH_REMATCH[1]} (check CUDA_VISIBLE_DEVICES)."
    fi
  fi
fi

# 6. Optional CODE-MANIFEST.sha256 integrity verification
if [[ -n "${CODE_DIR}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "${CODE_DIR%/}/CODE-MANIFEST.sha256" ]]; then
    if [[ -n "${PYTHON_BIN}" && -x "${PYTHON_BIN}" && -f "${SCRIPT_DIR}/prepare_code_release.py" ]]; then
      if "${PYTHON_BIN}" "${SCRIPT_DIR}/prepare_code_release.py" --verify "${CODE_DIR}"; then
        record_ok "Verified CODE-MANIFEST.sha256 in ${CODE_DIR}"
      else
        record_fail "CODE-MANIFEST.sha256 check failed in ${CODE_DIR}"
      fi
    elif command -v sha256sum >/dev/null 2>&1; then
      if (cd "${CODE_DIR}" && sha256sum -c CODE-MANIFEST.sha256 >/dev/null); then
        record_ok "Verified CODE-MANIFEST.sha256 via sha256sum in ${CODE_DIR}"
      else
        record_fail "sha256sum verification failed in ${CODE_DIR}"
      fi
    else
      record_fail "Neither Python verifier nor sha256sum is available to verify CODE-MANIFEST.sha256"
    fi
  else
    record_fail "Missing CODE-MANIFEST.sha256 in ${CODE_DIR}"
  fi
fi

echo "=== Summary: fails=${FAILS} warns=${WARNS} ==="
if (( FAILS > 0 )); then
  exit 1
fi
if (( STRICT == 1 && WARNS > 0 )); then
  exit 1
fi
exit 0
