#!/usr/bin/env bash
# Read-only environment check for LogAn. Installs and changes nothing.
# Network check: sends HTTPS requests (no data) to the hosts LogAn may need; set LOGAN_CHECK_NETWORK=0 to skip.
# Compatible with bash 3.2 (macOS). Usage: bash check_env.sh [path_to_LogAn_clone]

LOGAN_DIR="${1:-${LOGAN_HOME:-$HOME/tools/LogAn}}"
ok()   { printf "  [ok]   %s\n" "$*"; }
miss() { printf "  [--]   %s\n" "$*"; }
warn() { printf "  [!!]   %s\n" "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

NET_HF=1; HAS_CONTAINER=""; HAS_IMAGE=""; HAS_LOGAN=""; HAS_MCP=""; HAS_RUST=""; GOOD_PY=""

echo "== System"
ok "$(uname -s) $(uname -m)"

echo "== Containers"
for rt in podman docker; do
  if have "$rt"; then
    HAS_CONTAINER="$rt"
    ok "$rt: $($rt --version 2>/dev/null | head -1)"
    for img in ${LOGAN_IMAGE:-} ghcr.io/log-analyzer/logan:latest logan:latest localhost/logan:latest; do
      if info=$($rt image inspect "$img" --format 'id={{.Id}} user={{.Config.User}}' 2>/dev/null); then
        HAS_IMAGE="$rt"; ok "LogAn image present: $img ($info)"; break
      fi
    done
  fi
done
[ -z "$HAS_CONTAINER" ] && miss "neither podman nor docker found"
[ -n "$HAS_CONTAINER" ] && [ -z "$HAS_IMAGE" ] && miss "no LogAn image pulled"

echo "== Python and build tools"
for py in python3.11 python3.12 python3.10 python3.9 python3; do
  if have "$py"; then
    v=$($py -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
    case "$v" in
      3.9|3.10|3.11|3.12) ok "$py -> $v (suitable)"; [ -z "$GOOD_PY" ] && GOOD_PY="$py" ;;
      *) warn "$py -> $v (not suitable: LogAn needs 3.9–3.12, preferably 3.11)" ;;
    esac
  fi
done
[ -z "$GOOD_PY" ] && warn "no suitable Python (uv can fetch one: uv venv --python 3.11)"
if have uv; then ok "uv: $(uv --version 2>/dev/null)"; else miss "uv not found (needed for a local install)"; fi
if have git; then ok "git: $(git --version 2>/dev/null)"; else miss "git not found"; fi
if have cargo; then HAS_RUST=1; ok "rust: $(rustc --version 2>/dev/null)"; else miss "Rust not found (needed to build drain3 for a local install)"; fi
if have java; then ok "java: $(java -version 2>&1 | grep -i 'version' | head -1)"; else miss "java not found (only needed if pyjnius has to be built from source)"; fi

echo "== LogAn"
LOGAN_BIN=""; MCP_BIN=""
if have logan; then LOGAN_BIN="$(command -v logan)"; fi
[ -z "$LOGAN_BIN" ] && [ -x "$LOGAN_DIR/.venv/bin/logan" ] && LOGAN_BIN="$LOGAN_DIR/.venv/bin/logan"
if have logan-mcp; then MCP_BIN="$(command -v logan-mcp)"; fi
[ -z "$MCP_BIN" ] && [ -x "$LOGAN_DIR/.venv/bin/logan-mcp" ] && MCP_BIN="$LOGAN_DIR/.venv/bin/logan-mcp"
if [ -n "$LOGAN_BIN" ]; then
  HAS_LOGAN=1; ok "logan: $LOGAN_BIN ($("$LOGAN_BIN" --version 2>/dev/null | tail -1))"
else
  miss "logan CLI not found (looked in PATH and $LOGAN_DIR/.venv/bin)"
fi
if [ -n "$MCP_BIN" ]; then HAS_MCP=1; ok "logan-mcp: $MCP_BIN"; else miss "logan-mcp not found (needs a local install with the [mcp] extra)"; fi
[ -d "$LOGAN_DIR/.git" ] && ok "LogAn clone: $LOGAN_DIR"

echo "== Caches"
HF_DIR="${HF_HOME:-$HOME/.cache/huggingface}"
if ls -d "$HF_DIR"/hub/models--cross-encoder--nli-MiniLM2-L6-H768 >/dev/null 2>&1; then
  ok "crossencoder model cached for a local install ($HF_DIR)"
else
  miss "no crossencoder model in $HF_DIR (a local install downloads it from huggingface.co on first run)"
fi
CHF="${LOGAN_HF_CACHE:-$HOME/.cache/logan-hf}"
if ls -d "$CHF"/hub/models--* >/dev/null 2>&1; then ok "model cache for the container: $CHF"; else miss "container model cache empty ($CHF): warm it once with LOGAN_OFFLINE=0"; fi
if [ -d "$CHF" ] && [ -n "$(find "$CHF" -maxdepth 0 -perm -0002 2>/dev/null)" ]; then warn "$CHF is world-writable; fix: chmod o-w \"$CHF\""; fi
if ls -d "$HOME"/.cache/logan/duckdb-wasm/* >/dev/null 2>&1; then ok "DuckDB-WASM cached (~/.cache/logan)"; else miss "DuckDB-WASM not cached (a local install downloads it from cdn.jsdelivr.net; the image already has it)"; fi

echo "== Network (HTTPS, 5 s timeout)"
if [ "${LOGAN_CHECK_NETWORK:-1}" = 0 ]; then
  miss "skipped (LOGAN_CHECK_NETWORK=0)"
elif have curl; then
  for host in huggingface.co cdn.jsdelivr.net ghcr.io github.com download.pytorch.org; do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "https://$host" 2>/dev/null)
    case "$code" in
      000|"") warn "$host unreachable"; [ "$host" = huggingface.co ] && NET_HF="" ;;
      *) ok "$host (HTTP $code)" ;;
    esac
  done
else
  miss "curl not found, network check skipped"
fi

echo "== Recommendation"
if [ -n "$HAS_IMAGE" ]; then
  echo "  The LogAn image is present: use logan-analyze/scripts/logan_docker.sh (option A)."
  [ -n "$HAS_MCP" ] && echo "  logan-mcp is also installed: register $MCP_BIN in your agent if you want MCP tools (logan-setup, step 3)."
elif [ -n "$HAS_MCP" ]; then
  echo "  LogAn with MCP is installed. Register $MCP_BIN in your agent (logan-setup, step 3) if not done yet."
elif [ -n "$HAS_LOGAN" ]; then
  echo "  CLI present without MCP: reinstall LogAn with the 'mcp' extra (logan-setup, option B) if MCP tools are wanted."
elif [ -n "$HAS_CONTAINER" ]; then
  echo "  A container runtime is available: pull the image (option A), the simplest route."
elif [ -n "$HAS_RUST" ] && have uv; then
  echo "  Tools for a local install are present: option B (CLI and MCP), after the user approves it."
else
  echo "  Missing tools: install podman/docker (option A) or uv + Rust (option B). Ask the user which."
fi
if [ -z "$NET_HF" ] && ! ls -d "$CHF"/hub/models--* >/dev/null 2>&1 \
   && ! ls -d "$HF_DIR"/hub/models--cross-encoder--nli-MiniLM2-L6-H768 >/dev/null 2>&1; then
  echo "  Note: huggingface.co is unreachable and no model is cached, so classification cannot start."
  echo "  Options: a proxy or mirror (HF_ENDPOINT), copy a model cache from another machine, or --model-type custom."
fi
