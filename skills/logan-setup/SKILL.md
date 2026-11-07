---
name: logan-setup
description: Install, update, check and connect LogAn (IBM Research / Red Hat Log Analyzer, github.com/Log-Analyzer/LogAn) for a local agent — the ghcr.io/log-analyzer/logan container image, a local uv install, the logan-mcp MCP server, offline model cache, container permissions and install errors. Use when the user asks to install, set up, update, verify or troubleshoot LogAn / logan / logan-mcp, or when the logan-analyze skill finds LogAn missing.
---

# LogAn: install, check and connect

LogAn compresses logs into templates (Drain3), classifies each template with a small NLI model on CPU
(golden signal + fault category) and builds an interactive Log Explorer. This skill only gets LogAn working;
log analysis itself is the `logan-analyze` skill. Answer in the user's language.

## Rules

- Ask the user before installing system packages (Rust, a JDK, podman/docker) or pulling images.
  Packages inside a dedicated venv need no confirmation.
- Never install LogAn into the system Python or with `sudo pip`; use a dedicated venv.
- In agent configs always use the absolute path of the venv binary: agents do not activate venvs.
- Never make output or cache directories world-writable (`chmod a+rwx`); run the container as your user.

## Step 1. See what is there

```bash
bash <skill-dir>/scripts/check_env.sh                       # local, read-only; LOGAN_CHECK_NETWORK=0 skips the HTTPS probes
bash <skill-dir>/../logan-analyze/scripts/logan_docker.sh check   # image, CLI version inside it, run-as user, model cache
```

`check_env.sh` reports podman/docker and the LogAn image, Python 3.9–3.12, uv, git, Rust, Java, the
`logan`/`logan-mcp` binaries, model caches, reachability of the hosts LogAn needs, and MCP registration in
Claude Code / Codex, then prints a recommendation. If LogAn already works, go to step 4.

## Step 2. Choose an installation

| Goal | Option |
|---|---|
| Analyze logs from the command line, no MCP | **A. Container** (no Rust, Java or builds) |
| Agent calls LogAn as MCP tools `analyze_logs` / `extract_templates` | **B. Local install** |
| Custom tags or a custom model | either; tags work only through CLI or container, not MCP |

The image has no MCP server (the package is installed without the `[mcp]` extra), so MCP needs option B.

### A. Container

```bash
docker pull ghcr.io/log-analyzer/logan:latest        # or: podman pull ...
docker image inspect ghcr.io/log-analyzer/logan:latest --format '{{.Id}}'   # note the digest you run
```

Use `logan-analyze/scripts/logan_docker.sh` to run it: it mounts paths, keeps the model cache, runs offline by
default and runs the container as your user. The model is not part of the image. Warm the cache once with
network access on a non-sensitive file, then verify an offline run:

```bash
w=<skill-dir>/../logan-analyze/scripts/logan_docker.sh
LOGAN_OFFLINE=0 bash "$w" analyze -f /path/to/sample.log -o /tmp/logan-warmup-$$
bash "$w" analyze -f /path/to/sample.log -o /tmp/logan-offline-$$      # must succeed with --network none
```

Pitfalls:
- `HF_HOME=/tmp/hf_cache` inside the image. Without a volume there (the wrapper mounts `~/.cache/logan-hf`)
  the model is downloaded again on every `--rm` run.
- The image runs as UID 1001, GID 0 and cannot write to your directories. Do not `chmod a+rwx`; run as your
  UID: docker `--user "$(id -u):$(id -g)" --group-add 0`, rootless podman `--userns=keep-id`. The UBI image
  supports arbitrary UIDs (its app directory is accessible to group 0). As root the wrapper keeps the image
  user and creates output and cache with mode 770, group 0.
- `:z` is needed on SELinux hosts (Fedora/RHEL); the wrapper adds it automatically.

### B. Local install (needed for MCP)

Requirements: Python 3.9–3.12 (`numpy~=1.26` does not build on 3.13; 3.11 as in the image is best), `uv`,
`git`, a Rust toolchain (`drain3` is installed from the Drain3-rs git repository and built with maturin),
about 2 GB of disk for torch CPU and transformers.

```bash
git clone https://github.com/Log-Analyzer/LogAn.git ~/tools/LogAn
cd ~/tools/LogAn
uv venv --python 3.11
source .venv/bin/activate
# torch is not in requirements.txt, but the transformers classifier needs it:
uv pip install torch --index-url https://download.pytorch.org/whl/cpu   # Linux; on macOS: uv pip install torch
uv pip install -e ".[mcp]"
logan --version && which logan-mcp
logan analyze -f examples/Linux_2k.log -o /tmp/logan-smoke-$$      # smoke test
```

The first local run downloads `cross-encoder/nli-MiniLM2-L6-H768` from huggingface.co into
`~/.cache/huggingface` and DuckDB-WASM (~10 MB) from cdn.jsdelivr.net into `~/.cache/logan/duckdb-wasm/`;
afterwards `export HF_HUB_OFFLINE=1` works offline. Update: `git pull && uv pip install -e ".[mcp]"`.

## Step 3. Connect the MCP server (option B only)

Binary: `~/tools/LogAn/.venv/bin/logan-mcp` (replace `/home/USER` below with the real path). The first
`analyze_logs` call loads the model for 30–60 s and inference on large logs takes minutes, so the tool timeout
must be generous.

**Claude Code** (the default tool timeout is very long):
```bash
claude mcp add --transport stdio --scope user logan -- /home/USER/tools/LogAn/.venv/bin/logan-mcp
claude mcp list
```

**Codex CLI**, `~/.codex/config.toml` (default `tool_timeout_sec = 60` is too short):
```toml
[mcp_servers.logan]
command = "/home/USER/tools/LogAn/.venv/bin/logan-mcp"
startup_timeout_sec = 30
tool_timeout_sec = 1800
```

**Clients with an `mcpServers` JSON config** (Claude Desktop, Cursor, Gemini CLI, …):
```json
{ "mcpServers": { "logan": { "command": "/home/USER/tools/LogAn/.venv/bin/logan-mcp" } } }
```
If the client has a per-server timeout, set at least 15–30 minutes.

**HTTP transport** (agent on another machine): `logan-mcp --transport streamable-http`. FastMCP listens on
`127.0.0.1:8000`, endpoint `/mcp`; change the port with `FASTMCP_PORT` (`logan view` also uses 8000).

Restart the agent and check that `analyze_logs` and `extract_templates` are listed. To debug the server:
`cd ~/tools/LogAn && mcp dev logan/mcp/server.py` (MCP Inspector, needs Node.js).

## Step 4. Verify

1. Container: `logan_docker.sh check` shows the image, the CLI version and a non-empty model cache; an offline
   `analyze` on a sample finishes and `report` on its output works.
2. MCP: `extract_templates(files=["/home/USER/tools/LogAn/examples/Linux_2k.log"])` returns
   `status: success` within seconds; `analyze_logs` on the same file returns `golden_signal_distribution`.
3. Only then tell the user LogAn is ready and hand over to `logan-analyze`.

## Common errors

| Symptom | Cause | Fix |
|---|---|---|
| `drain3` build fails, `can't find Rust compiler` | no Rust | install rustup (ask first) or use the container |
| `numpy` build fails | venv on Python 3.13+ | recreate: `uv venv --python 3.11` |
| `pyjnius` build fails (`JAVA_HOME`) | no wheel for the platform | install JDK 11+ and set `JAVA_HOME`; LogAn's code does not import jnius, it is only a dependency |
| PyTorch error at step 3 of the run | torch missing | `uv pip install torch` (CPU index above) |
| Wrapper: "offline mode and the model cache is empty" | cold cache | one warm-up run with `LOGAN_OFFLINE=0` on a non-sensitive file |
| Model download hangs or fails | no access to huggingface.co | proxy / `HF_ENDPOINT` mirror / copy a cache from another machine, or `--model-type custom` |
| Local install fails at step 0 (`Preparing output directory`) with a download error | DuckDB-WASM is fetched from cdn.jsdelivr.net while preparing the output | allow the CDN or copy `~/.cache/logan/duckdb-wasm/`; the image already contains it |
| MCP call times out | model loading plus inference | raise the timeout (step 3); warm the server on a small file |
| `Address already in use` on 8000 | `logan view` and HTTP MCP both default to 8000 | `logan view -p 8080 ...` or `FASTMCP_PORT=8765` |
| `Permission denied` on /data/output | container user cannot write to your directory | run as your UID (`LOGAN_RUN_AS=auto`, the wrapper default); if a directory was made world-writable earlier: `chmod o-w,g-w <dir>` |
