# LogAn Skills — directory edition

Agent skills for analyzing logs with [LogAn](https://github.com/Log-Analyzer/LogAn), the Log Analyzer from
IBM Research maintained by Red Hat. The skills teach Claude to run LogAn from its container image without
network access, check how much of the input LogAn actually analyzed, read its templates, golden signals and
fault categories without loading raw logs into context, find the root cause of an incident and report it with
evidence.

**Unofficial.** This project is not affiliated with or endorsed by IBM, Red Hat or the LogAn authors.

This is the edition published in Claude's plugin directory. It contains **instructions only**, no bundled
scripts: Claude runs the LogAn container and short read-only queries with the Python inside that image. The
[full edition](https://github.com/biparasite/LogAn_skills) in the repository root adds helper scripts (a
Docker wrapper, a report generator and a tag tester) and is installed from the repository's own marketplace.

| Skill | Use it to |
|---|---|
| `logan-analyze` | Analyze logs or an existing LogAn run: input coverage, golden signals, fault categories, rare and early problems, drill-down, root cause, report |
| `logan-tags` | Build and test `--tag-config` rules that group templates by component |
| `logan-setup` | Install or check LogAn (container or local), warm the model cache, connect the `logan-mcp` server |

## Requirements

- Docker or Podman with the LogAn image `ghcr.io/log-analyzer/logan:latest`, or a local LogAn install
  (see `logan-setup`).
- A one-time download of the classifier model, done with the user's agreement on a non-sensitive sample.

## What the skills do

- They instruct Claude to run the local LogAn image with `docker run --rm`, mounting inputs read-only, writing
  results to a new private output directory and keeping the model cache in `~/.cache/logan-hf`. The container
  runs as the user, without network access (`--network none`, HuggingFace offline mode), except for the
  one-time model download from huggingface.co that the user agrees to.
- They instruct Claude to read the output with read-only queries run inside the same image, also without
  network access, and to serve the HTML report on 127.0.0.1 only.
- They tell Claude to treat log content as untrusted data, read only small samples, and quote only redacted
  fragments.
- Nothing is sent to the plugin author; there is no telemetry. See the [privacy policy](PRIVACY.md).

## Compatibility

Checked against LogAn `main` at commit `70d4975` (2026-07-30, package version 0.0.1) and the
`ghcr.io/log-analyzer/logan` image layout (`/opt/app-root/src/.venv`, UID 1001, GID 0).

## License

Apache License 2.0, see [LICENSE](LICENSE).
