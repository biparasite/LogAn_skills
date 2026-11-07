---
name: logan-setup
description: Install, update, check and connect LogAn (IBM Research / Red Hat Log Analyzer, github.com/Log-Analyzer/LogAn) for a local agent — the ghcr.io/log-analyzer/logan container image, a local uv install, the logan-mcp MCP server, offline model cache, container permissions and install errors. Use when the user asks to install, set up, update, verify or troubleshoot LogAn / logan / logan-mcp, or when the logan-analyze skill finds LogAn missing.
---

# LogAn: install, check and connect

LogAn compresses logs into templates (Drain3), classifies each template with a small NLI model on CPU
(golden signal + fault category) and builds an interactive Log Explorer. This skill only gets LogAn working;
log analysis itself is the `logan-analyze` skill. Answer in the user's language.

## Rules

- Ask the user before installing anything: system packages (Rust, a JDK, podman/docker), container images,
  or LogAn itself. Installing downloads and runs third-party code.
- Never install LogAn into the system Python or with `sudo pip`; use a dedicated venv.
- In agent configs always use the absolute path of the venv binary: agents do not activate venvs.
- Never make output or cache directories world-writable (`chmod a+rwx`); run the container as your user.

## Step 1. See what is there

```bash
bash <skill-dir>/scripts/check_env.sh                       # local, read-only; LOGAN_CHECK_NETWORK=0 skips the HTTPS probes
bash <skill-dir>/../logan-analyze/scripts/logan_docker.sh check   # image, CLI version inside it, run-as user, model cache
```

`check_env.sh` reports podman/docker and the LogAn image, Python 3.9–3.12, uv, git, Rust, Java, the
`logan`/`logan-mcp` binaries, model caches and reachability of the hosts LogAn needs, then prints a
recommendation. It does not read agent configuration files. If LogAn already works, go to step 4.

## Step 2. Choose an installation

| Goal | Option |
|---|---|
| Analyze logs from the command line, no MCP | **A. Container** (no Rust, Java or builds) |
| Agent calls LogAn as MCP tools `analyze_logs` / `extract_templates` | **B. Local install** |
| Custom tags or a custom model | either; tags work only through CLI or container, not MCP |

The image has no MCP server (the package is installed without the `[mcp]` extra), so MCP needs option B.

### A. Container

If the image is missing, ask the user before pulling it (`docker pull ghcr.io/log-analyzer/logan:latest`, or
`podman pull`), and note the image ID you run (`docker image inspect <image> --format '{{.Id}}'`).

Use `logan-analyze/scripts/logan_docker.sh` to run it: it mounts paths, keeps the model cache, runs offline by
default and runs the container as your user. The model is not part of the image. Warm the cache once with
network access on a non-sensitive file, then verify an offline run:

```bash
LOGAN_OFFLINE=0 bash <skill-dir>/../logan-analyze/scripts/logan_docker.sh analyze -f /path/to/sample.log -o /tmp/logan-warmup
bash <skill-dir>/../logan-analyze/scripts/logan_docker.sh analyze -f /path/to/sample.log -o /tmp/logan-offline   # must succeed with --network none
```

Pitfalls:
- `HF_HOME=/tmp/hf_cache` inside the image. Without a volume there (the wrapper mounts `~/.cache/logan-hf`)
  the model is downloaded again on every `--rm` run.
- The image runs as UID 1001, GID 0 and cannot write to your directories. Do not `chmod a+rwx`; run as your
  UID: docker `--user <uid>:<gid> --group-add 0` (your `id -u` and `id -g`), rootless podman `--userns=keep-id`. The UBI image
  supports arbitrary UIDs (its app directory is accessible to group 0). As root the wrapper keeps the image
  user and creates output and cache with mode 770, group 0.
- `:z` is needed on SELinux hosts (Fedora/RHEL); the wrapper adds it automatically.

### B. Local install (needed for MCP)

A local install downloads and builds third-party code, so never run it on your own initiative: show the user
the plan and get explicit approval first. Follow the "Option 2 - Local" section of the
[LogAn README](https://github.com/Log-Analyzer/LogAn), preferably at the commit these skills were checked
against (`70d4975`), with these adjustments the README does not mention:

- Use a dedicated venv (for example `~/tools/LogAn/.venv`), never the system Python.
- Use Python 3.9–3.12: `numpy~=1.26` does not build on 3.13; 3.11, as in the image, is the safest choice.
- A Rust toolchain is required: `drain3` is installed from the Drain3-rs git repository and built with maturin.
- `torch` is not in `requirements.txt`, but the transformers classifier needs it: install the CPU build of
  torch into the same venv.
- Install the package with the `mcp` extra to get the `logan-mcp` binary.
- Expect about 2 GB of disk for torch CPU and transformers.

Done when `logan --version` and `which logan-mcp` succeed in that venv and a run on the bundled
`examples/Linux_2k.log` into a new output directory finishes.

The first local run downloads `cross-encoder/nli-MiniLM2-L6-H768` from huggingface.co into
`~/.cache/huggingface` and DuckDB-WASM (~10 MB) from cdn.jsdelivr.net into `~/.cache/logan/duckdb-wasm/`;
afterwards `HF_HUB_OFFLINE=1` works offline. To update, repeat the same steps from an updated checkout, again
with the user's approval.

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

Restart the agent and check that `analyze_logs` and `extract_templates` are listed, using the client's own
MCP listing (for example `claude mcp list`) rather than reading its configuration files. To debug the server,
the MCP Inspector can be run on `logan/mcp/server.py` from the LogAn checkout (needs Node.js).

## Step 4. Verify

1. Container: `logan_docker.sh check` shows the image, the CLI version and a non-empty model cache; an offline
   `analyze` on a sample finishes and `report` on its output works.
2. MCP: `extract_templates(files=["/home/USER/tools/LogAn/examples/Linux_2k.log"])` returns
   `status: success` within seconds; `analyze_logs` on the same file returns `golden_signal_distribution`.
3. Only then tell the user LogAn is ready and hand over to `logan-analyze`.

## Common errors

| Symptom | Cause | Fix |
|---|---|---|
| `drain3` build fails, `can't find Rust compiler` | no Rust | install a Rust toolchain (with the user's approval) or use the container |
| `numpy` build fails | venv on Python 3.13+ | recreate the venv with Python 3.11 |
| `pyjnius` build fails (`JAVA_HOME`) | no wheel for the platform | install JDK 11+ and set `JAVA_HOME`; LogAn's code does not import jnius, it is only a dependency |
| PyTorch error at step 3 of the run | torch missing | install the CPU build of torch into the LogAn venv |
| Wrapper: "offline mode and the model cache is empty" | cold cache | one warm-up run with `LOGAN_OFFLINE=0` on a non-sensitive file |
| Model download hangs or fails | no access to huggingface.co | proxy / `HF_ENDPOINT` mirror / copy a cache from another machine, or `--model-type custom` |
| Local install fails at step 0 (`Preparing output directory`) with a download error | DuckDB-WASM is fetched from cdn.jsdelivr.net while preparing the output | allow the CDN or copy `~/.cache/logan/duckdb-wasm/`; the image already contains it |
| MCP call times out | model loading plus inference | raise the timeout (step 3); warm the server on a small file |
| `Address already in use` on 8000 | `logan view` and HTTP MCP both default to 8000 | `logan view -p 8080 ...` or `FASTMCP_PORT=8765` |
| `Permission denied` on /data/output | container user cannot write to your directory | run as your UID (`LOGAN_RUN_AS=auto`, the wrapper default); if a directory was made world-writable earlier: `chmod o-w,g-w <dir>` |
