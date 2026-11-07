# Privacy policy — LogAn Skills

Effective date: 2026-11-08

LogAn Skills is an unofficial, open-source set of agent skills and helper scripts for analyzing logs with
[LogAn](https://github.com/Log-Analyzer/LogAn). This policy describes what the plugin does with data.

## What the plugin processes

- The plugin processes only the log files and directories that the user explicitly points it at.
  Logs can contain personal data, such as usernames, IP addresses or e-mail addresses.
- Processing happens locally, in the LogAn Docker/Podman container on the user's own machine (or in a local
  LogAn installation).
- The agent reads only small samples and summaries of the logs, and is instructed to quote only the lines it
  needs, redacted where they contain secrets or personal data.

## What the plugin stores

- Analysis results (a LogAn output directory with a Parquet store and an HTML report) are written to a local
  directory the user chooses, with private permissions.
- The classifier model is cached locally in `~/.cache/logan-hf`.
- Nothing is stored anywhere else. Local files remain until the user deletes them.

## What the plugin sends

- The plugin author collects no data: there is no telemetry, analytics or remote logging.
- Log content and analysis results are never sent anywhere by the plugin. By default the container runs
  without network access (`--network none`).
- Network access happens only in these cases:
  - when the user explicitly sets `LOGAN_OFFLINE=0`, the container downloads the classifier model from
    huggingface.co (download only; HuggingFace telemetry is disabled);
  - the optional `check_env.sh` sends plain HTTPS requests without any payload to huggingface.co,
    cdn.jsdelivr.net, ghcr.io, github.com and download.pytorch.org to report reachability
    (skip with `LOGAN_CHECK_NETWORK=0`);
  - the report viewer serves the local output directory on 127.0.0.1 only.
- These third-party services, and the AI assistant you use the skills with, have their own privacy policies.

## Children

The plugin is a tool for engineers and is not intended for people under 18.

## Contact and changes

Questions and reports: [GitHub issues](https://github.com/biparasite/LogAn_skills/issues).
Changes to this policy are published in this file in the repository.
