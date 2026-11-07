# LogAn Skills

Agent skills for analyzing logs with [LogAn](https://github.com/Log-Analyzer/LogAn), the Log Analyzer from
IBM Research maintained by Red Hat. The skills teach an AI coding agent (Claude Code, Codex, Hermes, Cursor and
others that read `SKILL.md`) to run LogAn from its container image, read its output without loading raw logs
into context, check how much of the input LogAn actually analyzed, find the root cause of an incident and report
it with evidence.

**Unofficial.** This project is not affiliated with or endorsed by IBM, Red Hat or the LogAn authors.

| Skill | Use it to |
|---|---|
| [`logan-analyze`](skills/logan-analyze/SKILL.md) | Analyze logs or an existing LogAn run: input coverage, golden signals, fault categories, rare and early problems, drill-down, root cause, report |
| [`logan-tags`](skills/logan-tags/SKILL.md) | Build and test `--tag-config` rules that group templates by component |
| [`logan-setup`](skills/logan-setup/SKILL.md) | Install or check LogAn (container or local), warm the model cache, connect the `logan-mcp` server |

## Requirements

- Docker or Podman with the LogAn image: `docker pull ghcr.io/log-analyzer/logan:latest`
  (or a local LogAn install, see `logan-setup`)
- bash (3.2+ works, so the macOS default is fine)
- Python 3 on the host is optional: it is used by `tag_tool.py`, `check_env.sh` and `view`.
  The report script runs inside the LogAn image, so the host needs no pyarrow.

## Install

**Claude Code plugin**
```bash
claude plugin marketplace add biparasite/LogAn_skills
claude plugin install logan-skills@logan-skills
```
Skills are then available as `logan-skills:logan-analyze`, `logan-skills:logan-tags` and `logan-skills:logan-setup`.

**Any agent, via the skills CLI**
```bash
npx skills add biparasite/LogAn_skills
```

**Manual copy**: copy the three folders from `skills/` into your agent's skills directory, for example
`~/.claude/skills/` (Claude Code), `.agents/skills/` (Codex and other agents that read it) or
`~/.hermes/skills/devops/` (Hermes). Agents without skill support can be pointed at
`skills/logan-analyze/SKILL.md` from `AGENTS.md`.

## Quick start

```bash
w=skills/logan-analyze/scripts/logan_docker.sh
bash "$w" check                                                      # image, CLI version, run-as user, model cache
LOGAN_OFFLINE=0 bash "$w" analyze -f sample.log -o /tmp/logan-warmup # one-time model download, non-sensitive file
bash "$w" analyze -f /var/log/myapp/ --process-all-files -o ./logan-runs/incident-1   # offline by default
bash "$w" report ./logan-runs/incident-1                             # summary with input coverage
bash "$w" report ./logan-runs/incident-1 --template 42               # drill down into one template
```

Or ask your agent: *"Analyze the logs in /var/log/myapp with LogAn and tell me what broke around 14:05."*

## What the scripts do

Everything runs locally. The scripts send no telemetry.

- `logan_docker.sh` runs the local LogAn image with `docker run --rm`. Inputs are mounted read-only.
  The output goes to a new directory created with mode 700 (770 with group 0 when run as root).
  The model cache lives in `~/.cache/logan-hf`. The container runs as your UID.
  - **Network:** by default the container has none (`--network none`, HuggingFace offline mode).
  - With `LOGAN_OFFLINE=0`, the container may download the classifier model from huggingface.co.
    This is the only network access the skills initiate during analysis. HuggingFace telemetry is disabled.
  - `view` serves the output directory over HTTP on 127.0.0.1.
- `logan_report.py` reads a LogAn output directory and prints a summary or a drill-down.
  It writes nothing and makes no network calls.
- `tag_tool.py` reads log files and a tag config and prints coverage. It writes nothing and makes no network calls.
- `check_env.sh` is read-only:
  - it inspects local tools, images, caches and agent MCP configs (`~/.claude.json`, `~/.codex/config.toml`);
  - it sends plain HTTPS requests, with no data, to huggingface.co, cdn.jsdelivr.net, ghcr.io, github.com
    and download.pytorch.org to report reachability. Set `LOGAN_CHECK_NETWORK=0` to skip them.
- The skills tell the agent to treat log content as untrusted data, to read only small samples, and to quote
  only redacted fragments.

## Compatibility

Checked against LogAn `main` at commit `70d4975` (2026-07-30, package version 0.0.1) and the
`ghcr.io/log-analyzer/logan` image layout (`/opt/app-root/src/.venv`, UID 1001, GID 0).

## License and notices

Apache License 2.0, see [LICENSE](LICENSE). `skills/logan-tags/scripts/tag_tool.py` reimplements the token
extraction and scoring logic of LogAn's `idm_component_tagger` (Apache License 2.0, IBM Research / Red Hat)
so that tag rules can be tested without running LogAn.
