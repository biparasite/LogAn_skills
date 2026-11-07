#!/usr/bin/env bash
# logan_docker.sh — run LogAn from its Docker/Podman image without hand-written volume mounts.
# Compatible with bash 3.2 (macOS).
#
#   logan_docker.sh check
#   logan_docker.sh analyze [logan analyze flags] -f FILE_OR_DIR [-f ...] [-o NEW_OUTPUT_DIR]
#   logan_docker.sh report  OUTPUT_DIR [logan_report.py arguments]
#   logan_docker.sh view    OUTPUT_DIR [PORT]      (blocks the terminal; run it in the background)
#
# All paths are host paths. The script mounts inputs read-only, plus the output directory,
# an optional tag config and an optional custom model script.
#
# Environment:
#   LOGAN_IMAGE     image (default: first found of ghcr.io/log-analyzer/logan:latest, logan:latest, localhost/logan:latest)
#   LOGAN_RUNTIME   docker | podman (default: whichever is installed)
#   LOGAN_HF_CACHE  host directory for the HuggingFace model cache (default ~/.cache/logan-hf)
#   LOGAN_OFFLINE   1 (default): the container gets no network (--network none) and HF offline mode;
#                   0: network allowed, e.g. to download the classifier model once
#   LOGAN_TZ        time zone inside the container (default UTC). LogAn interprets timestamps without
#                   a zone in this zone, so use the same LOGAN_TZ for analyze and report.
#   LOGAN_RUN_AS    auto (default) | image | UID:GID — which user the container runs as:
#                     auto: your UID (docker/rootful podman: --user UID:GID --group-add 0;
#                           rootless podman: --userns=keep-id --user UID:GID); as root: the image user
#                     image: the image user (UID 1001, GID 0)
#
# The script never makes directories world-writable. It creates the output directory (and the
# model cache on first use) with mode 700, or 770 with group 0 when run as root, so the
# image user (UID 1001, GID 0) can write to it.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/app-root/src
LOGAN_IN_IMG="$APP/.venv/bin/logan"
PY_IN_IMG="$APP/.venv/bin/python"

die() { echo "logan_docker: $*" >&2; exit 2; }
abspath() {  # realpath is missing on older macOS
  if [ -d "$1" ]; then (cd "$1" && pwd); else echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; fi
}

RT="${LOGAN_RUNTIME:-}"
if [ -z "$RT" ]; then
  if command -v docker >/dev/null 2>&1; then RT=docker; elif command -v podman >/dev/null 2>&1; then RT=podman; else die "neither docker nor podman is installed"; fi
fi
IMG="${LOGAN_IMAGE:-}"
need_image() {
  if [ -n "$IMG" ]; then
    "$RT" image inspect "$IMG" >/dev/null 2>&1 || die "image not found locally: $IMG"
    return 0
  fi
  for c in ghcr.io/log-analyzer/logan:latest logan:latest localhost/logan:latest; do
    if "$RT" image inspect "$c" >/dev/null 2>&1; then IMG="$c"; return 0; fi
  done
  die "no LogAn image found. Set LOGAN_IMAGE=... or run: $RT pull ghcr.io/log-analyzer/logan:latest"
}
Z=""
if command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled 2>/dev/null; then Z=",z"; fi
HF_CACHE="${LOGAN_HF_CACHE:-$HOME/.cache/logan-hf}"

# Which user the container runs as. The UBI-based image supports arbitrary UIDs with group 0
# (its app directory and the bundled DuckDB-WASM cache are group-0 accessible).
USER_ARGS=()
case "${LOGAN_RUN_AS:-auto}" in
  auto)
    if [ "$(id -u)" -ne 0 ]; then
      if [ "$RT" = podman ] && [ "$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null)" = "true" ]; then
        USER_ARGS=(--userns=keep-id --user "$(id -u):$(id -g)")
      else
        USER_ARGS=(--user "$(id -u):$(id -g)" --group-add 0)
      fi
    fi ;;
  image) ;;
  *:*) USER_ARGS=(--user "$LOGAN_RUN_AS" --group-add 0) ;;
  *) die "LOGAN_RUN_AS must be auto, image or UID:GID, not '${LOGAN_RUN_AS}'" ;;
esac

# Private directory: 700 for a normal user; as root 770 + group 0 for the image user (GID 0).
create_private_dir() {
  local mode=700
  [ "$(id -u)" -ne 0 ] || mode=770
  mkdir -m "$mode" -- "$1" || die "cannot create directory: $1"
  if [ "$(id -u)" -eq 0 ]; then chgrp 0 -- "$1" || die "cannot set group 0 on: $1"; fi
}

show() { printf '+'; printf ' %q' "$@"; printf '\n'; } >&2

cache_has_models() { ls -d "$HF_CACHE"/hub/models--* >/dev/null 2>&1; }

cmd_check() {
  need_image
  echo "runtime: $RT ($("$RT" --version 2>/dev/null | head -1))"
  echo "image:   $IMG ($("$RT" image inspect "$IMG" --format 'id={{.Id}} user={{.Config.User}}' 2>/dev/null))"
  echo "run as:  ${LOGAN_RUN_AS:-auto}${USER_ARGS[*]+ (${USER_ARGS[*]})}"
  echo "offline: ${LOGAN_OFFLINE:-1}"
  "$RT" run --rm --network none ${USER_ARGS[@]+"${USER_ARGS[@]}"} --entrypoint "$LOGAN_IN_IMG" "$IMG" --version || return 1
  if [ -n "$(find "$HF_CACHE" -maxdepth 0 -perm -0002 2>/dev/null)" ]; then
    echo "WARNING: $HF_CACHE is world-writable. Fix it: chmod o-w \"$HF_CACHE\""
  fi
  if cache_has_models; then
    echo "model cache ($HF_CACHE):"; ls -d "$HF_CACHE"/hub/models--* | sed 's|.*/models--|  |; s|--|/|g'
  else
    echo "model cache is empty ($HF_CACHE): warm it once with LOGAN_OFFLINE=0 on a synthetic or non-sensitive file"
  fi
}

cmd_analyze() {
  need_image
  local mounts=() args=() n=0 out="" host model_type="" model="" glob_pat="" map=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -f|--files)       [ $# -ge 2 ] || die "$1 needs a value"; host="$(abspath "$2")"; shift 2 ;;
      --files=*)        host="$(abspath "${1#*=}")"; shift ;;
      -g|--glob)        [ $# -ge 2 ] || die "$1 needs a value"; glob_pat="$2"; shift 2; continue ;;
      --glob=*)         glob_pat="${1#*=}"; shift; continue ;;
      -o|--output-dir)  [ $# -ge 2 ] || die "$1 needs a value"; out="$2"; shift 2; continue ;;
      --output-dir=*)   out="${1#*=}"; shift; continue ;;
      --tag-config)     [ $# -ge 2 ] || die "$1 needs a value"
                        [ -f "$2" ] || die "tag config not found: $2"
                        mounts=( "${mounts[@]+"${mounts[@]}"}" -v "$(abspath "$2"):/data/config/tags.json:ro$Z" )
                        args=( "${args[@]+"${args[@]}"}" --tag-config /data/config/tags.json ); shift 2; continue ;;
      --model-type)     [ $# -ge 2 ] || die "$1 needs a value"; model_type="$2"; args=( "${args[@]+"${args[@]}"}" "$1" "$2" ); shift 2; continue ;;
      -m|--model)       [ $# -ge 2 ] || die "$1 needs a value"; model="$2"; shift 2; continue ;;
      --model=*)        model="${1#*=}"; shift; continue ;;
      *)                args=( "${args[@]+"${args[@]}"}" "$1" ); shift; continue ;;
    esac
    [ -e "$host" ] || die "no such path: $host"
    n=$((n+1))
    if [ -d "$host" ]; then
      map="$map/data/in/$n	$host	dir
"
      mounts=( "${mounts[@]+"${mounts[@]}"}" -v "$host:/data/in/$n:ro$Z" ); args=( "${args[@]+"${args[@]}"}" -f "/data/in/$n" )
    else
      map="$map/data/in/$n	$host	file
"
      mounts=( "${mounts[@]+"${mounts[@]}"}" -v "$host:/data/in/$n/$(basename "$host"):ro$Z" )
      args=( "${args[@]+"${args[@]}"}" -f "/data/in/$n/$(basename "$host")" )
    fi
  done
  if [ -n "$glob_pat" ]; then   # expanded on the host: host paths do not exist inside the container
    local matched=0 g
    local IFS=''
    for g in $glob_pat; do
      [ -e "$g" ] || continue
      matched=1; n=$((n+1)); host="$(abspath "$g")"
      map="$map/data/in/$n	$host	file
"
      mounts=( "${mounts[@]+"${mounts[@]}"}" -v "$host:/data/in/$n/$(basename "$host"):ro$Z" )
      args=( "${args[@]+"${args[@]}"}" -f "/data/in/$n/$(basename "$host")" )
    done
    [ $matched = 1 ] || die "glob '$glob_pat' matched nothing"
  fi
  [ $n -gt 0 ] || die "no input: pass -f PATH (repeatable) or -g 'pattern'"
  if [ -n "$model" ]; then
    case "$model" in
      *.py:*) local mf="${model%%:*}" cls="${model##*:}"
              [ -f "$mf" ] || die "model script not found: $mf"
              mounts=( "${mounts[@]+"${mounts[@]}"}" -v "$(abspath "$(dirname "$mf")"):/data/model:ro$Z" )
              args=( "${args[@]+"${args[@]}"}" --model "/data/model/$(basename "$mf"):$cls" ) ;;
      *)      args=( "${args[@]+"${args[@]}"}" --model "$model" ) ;;
    esac
  fi
  case "${LOGAN_OFFLINE:-1}" in
    1) if [ "$model_type" != custom ] && ! cache_has_models; then
         die "offline mode and the model cache ($HF_CACHE) is empty. Warm it once: LOGAN_OFFLINE=0 $0 analyze -f <non-sensitive sample> -o <new dir>"
       fi ;;
    0) ;;
    *) die "LOGAN_OFFLINE must be 0 or 1" ;;
  esac
  if [ -z "$out" ]; then
    local runs; runs="$(pwd)/logan-runs"   # default: ./logan-runs under the current directory
    mkdir -p -m 700 -- "$runs" || die "cannot create $runs"
    out="$runs/$(date +%Y%m%d_%H%M%S)-$$"
  fi
  [ ! -e "$out" ] && [ ! -L "$out" ] || die "output must be a new path (LogAn may delete or overwrite files in it): $out"
  mkdir -p -m 700 -- "$(dirname "$out")" || die "cannot create the output parent"
  create_private_dir "$out"
  out="$(abspath "$out")"
  mkdir -p -m 700 -- "$(dirname "$HF_CACHE")" || die "cannot create the cache parent"
  [ -d "$HF_CACHE" ] || create_private_dir "$HF_CACHE"

  local BASE=( "$RT" run --rm ${USER_ARGS[@]+"${USER_ARGS[@]}"} -e "TZ=${LOGAN_TZ:-UTC}"
               -e HF_HOME=/tmp/hf_cache -e HF_HUB_DISABLE_TELEMETRY=1 -v "$HF_CACHE:/tmp/hf_cache:rw$Z" )
  [ "${LOGAN_OFFLINE:-1}" = 1 ] && BASE=( "${BASE[@]}" --network none -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 )
  local cmd=( "${BASE[@]}" -v "$out:/data/output:rw$Z" ${mounts[@]+"${mounts[@]}"} --entrypoint "$LOGAN_IN_IMG" "$IMG"
              analyze "${args[@]}" -o /data/output )
  show "${cmd[@]}"
  "${cmd[@]}"; local rc=$?
  printf '%s' "$map" > "$out/logan_docker_inputs.tsv" 2>/dev/null   # container path <TAB> host path <TAB> file|dir
  echo "OUTPUT_DIR=$out"
  if [ $rc -ne 0 ]; then
    echo "logan_docker: analysis failed (exit $rc), see the output above" >&2
    echo "logan_docker: on 'Permission denied' check LOGAN_RUN_AS (now: ${LOGAN_RUN_AS:-auto}${USER_ARGS[*]+, flags: ${USER_ARGS[*]}})" >&2
  elif [ ! -f "$out/store/templates.parquet" ]; then
    echo "logan_docker: the run finished but store/templates.parquet is missing, see the warnings above" >&2
    rc=1
  fi
  return $rc
}

cmd_report() {
  [ $# -ge 1 ] || die "usage: report OUTPUT_DIR [arguments]"
  need_image
  local out; out="$(abspath "$1")"; shift
  [ -d "$out" ] || die "no such directory: $out"
  # stdout carries only logan_report.py output (pure JSON with --format json); it maps container paths itself
  local cmd=( "$RT" run --rm --network none ${USER_ARGS[@]+"${USER_ARGS[@]}"} -e "TZ=${LOGAN_TZ:-UTC}"
              -v "$out:/data/output:ro$Z" -v "$SCRIPT_DIR:/skill:ro$Z"
              --entrypoint "$PY_IN_IMG" "$IMG" /skill/logan_report.py /data/output "$@" )
  "${cmd[@]}"
}

cmd_view() {
  [ $# -ge 1 ] || die "usage: view OUTPUT_DIR [PORT]"
  local out port; out="$(abspath "$1")"; port="${2:-8000}"
  [ -f "$out/log_diagnosis/explorer.html" ] || die "missing $out/log_diagnosis/explorer.html"
  echo "Log Explorer: http://localhost:$port/log_diagnosis/explorer.html  (Ctrl+C to stop)"
  local srv='import functools,http.server as h,sys
H=h.SimpleHTTPRequestHandler
H.extensions_map.update({".mjs":"text/javascript",".js":"text/javascript",".wasm":"application/wasm"})
h.test(HandlerClass=functools.partial(H,directory=sys.argv[1]),port=int(sys.argv[2]),bind=sys.argv[3])'
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "$srv" "$out" "$port" 127.0.0.1
  else
    need_image
    "$RT" run --rm ${USER_ARGS[@]+"${USER_ARGS[@]}"} -p "127.0.0.1:$port:8000" -v "$out:/data/output:ro$Z" \
      --entrypoint "$PY_IN_IMG" "$IMG" -c "$srv" /data/output 8000 0.0.0.0
  fi
}

sub="${1:-}"; [ $# -gt 0 ] && shift
case "$sub" in
  check)   cmd_check ;;
  analyze) cmd_analyze "$@" ;;
  report)  cmd_report "$@" ;;
  view)    cmd_view "$@" ;;
  *) sed -n '2,27p' "$0"; exit 2 ;;
esac
