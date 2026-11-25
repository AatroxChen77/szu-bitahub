#!/usr/bin/env bash
# Check bita CLI installation, OS compatibility, proxy conflicts, and optionally
# execute a bita command with proxy variables stripped for the single invocation.
set -euo pipefail

BIN_NAME="bita"
EXPLICIT_BIN=""
RUN_CMD=0

usage() {
  cat <<'EOF'
Usage:
  check_bita.sh [--bin /path/to/bita]
  check_bita.sh [--bin /path/to/bita] -- <bita-args...>

Examples:
  bash check_bita.sh
  bash check_bita.sh -- download -e <endpoint> -b home -c <clusterID> -o <remote> -l <local>
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bin)
      [[ $# -ge 2 ]] || { echo "[FAIL] --bin requires a path" >&2; exit 2; }
      EXPLICIT_BIN="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      RUN_CMD=1
      shift
      break
      ;;
    *)
      echo "[FAIL] Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

echo "=== [1/4] Host Platform ==="
UNAME_S="$(uname -s || true)"
UNAME_M="$(uname -m || true)"
echo "OS=${UNAME_S} ARCH=${UNAME_M}"

echo "=== [2/4] Locating bita CLI ==="
BITA_BIN=""
if [[ -n "${EXPLICIT_BIN}" ]]; then
  if [[ -x "${EXPLICIT_BIN}" ]]; then
    BITA_BIN="${EXPLICIT_BIN}"
  else
    echo "[FAIL] Explicit --bin is not executable: ${EXPLICIT_BIN}" >&2
    exit 1
  fi
elif command -v "${BIN_NAME}" >/dev/null 2>&1; then
  BITA_BIN="$(command -v "${BIN_NAME}")"
elif [[ -x "/usr/bin/bita" ]]; then
  BITA_BIN="/usr/bin/bita"
elif [[ -x "${HOME}/.local/bin/bita" ]]; then
  BITA_BIN="${HOME}/.local/bin/bita"
fi

if [[ -z "${BITA_BIN}" ]]; then
  echo "[MISSING] bita binary was not found in PATH, /usr/bin/bita, or \$HOME/.local/bin/bita."
  echo
  echo "Install instructions (user-level Linux, no sudo required):"
  echo "  1. Download the Linux client from SZU BitaHub Web UI:"
  echo "     「资源管理 -> 文件存储 -> 命令行快传工具 -> 下载」"
  echo "  2. Install to \$HOME/.local/bin:"
  echo "     mkdir -p \"\$HOME/.local/bin\""
  echo "     install -m 755 ./bita \"\$HOME/.local/bin/bita\""
  echo "     export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo "  3. Persist in ~/.bashrc if needed, then verify: bita --help"
  exit 1
fi

echo "[FOUND] ${BITA_BIN}"
ls -lh "${BITA_BIN}" || true
RESOLVED_PATH="$(command -v "${BIN_NAME}" 2>/dev/null || true)"
if [[ -z "${RESOLVED_PATH}" ]]; then
  echo "[WARN] ${BITA_BIN} is not in current PATH; add $(dirname "${BITA_BIN}") to PATH."
elif [[ "${RESOLVED_PATH}" != "${BITA_BIN}" ]]; then
  echo "[WARN] ${BITA_BIN} is not the active '${BIN_NAME}' in PATH (resolves to ${RESOLVED_PATH}); add $(dirname "${BITA_BIN}") ahead in PATH if needed."
fi

echo "=== [3/4] Proxy Environment Check ==="
SET_PROXY_VARS=()
for var in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY; do
  if [[ -n "${!var:-}" ]]; then
    SET_PROXY_VARS+=("${var}")
  fi
done

if [[ "${#SET_PROXY_VARS[@]}" -gt 0 ]]; then
  echo "[WARN] Proxy variables active (${SET_PROXY_VARS[*]}). Proxy env vars can cause bita upload EOF."
  echo "[INFO] Use 'env -u ... bita <args>' instead of editing global server proxy settings."
else
  echo "[OK] No active proxy environment variables detected."
fi

run_clean_bita() {
  env -u http_proxy -u https_proxy \
      -u HTTP_PROXY -u HTTPS_PROXY \
      -u all_proxy -u ALL_PROXY \
      -u no_proxy -u NO_PROXY \
      "${BITA_BIN}" "$@"
}

echo "=== [4/4] Verifying bita Subcommands ==="
HELP_OUT="$(run_clean_bita --help 2>&1 || true)"
if [[ -z "${HELP_OUT}" ]]; then
  echo "[FAIL] '${BITA_BIN} --help' returned empty output." >&2
  exit 1
fi
for sub in login upload download apply; do
  if ! grep -qw "${sub}" <<<"${HELP_OUT}"; then
    echo "[WARN] Expected subcommand '${sub}' not listed in --help output." >&2
  fi
done
echo "[OK] Verified bita CLI (--help lists core subcommands; status/logs/stop remain Web-UI actions)."

if [[ "${RUN_CMD}" -eq 1 ]]; then
  if [[ $# -eq 0 ]]; then
    echo "[FAIL] '--' provided without any bita arguments." >&2
    exit 2
  fi
  echo "[EXEC] Running clean-proxy bita $1 ..."
  set +e
  OUT="$(run_clean_bita "$@" 2>&1)"
  RC=$?
  set -e
  echo "${OUT}"
  if [[ ${RC} -ne 0 ]]; then
    if grep -qE "401|1027|认证" <<<"${OUT}"; then
      echo "[HINT] User-center JWT may have expired even if S3 download worked earlier." >&2
      echo "[HINT] Re-login with a fresh Web-copied 'bita login -u ... -p ... -t szdx -e ...' command." >&2
    fi
    exit "${RC}"
  fi
fi
