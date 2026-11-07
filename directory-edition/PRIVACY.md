# Privacy policy — LogAn Skills (directory edition)

Effective date: 2026-11-08

LogAn Skills is an unofficial, open-source set of agent skills for analyzing logs with
[LogAn](https://github.com/Log-Analyzer/LogAn). This edition contains instructions only, no bundled code.
This policy describes what happens to data when Claude follows these skills.

## What is processed

- Only the log files and directories that the user explicitly points Claude at. Logs can contain personal
  data, such as usernames, IP addresses or e-mail addresses.
- Processing happens locally, in the LogAn Docker/Podman container on the user's own machine (or in a local
  LogAn installation).
- Claude reads only small samples and summaries of the logs, and is instructed to quote only the lines it
  needs, redacted where they contain secrets or personal data.

## What is stored

- Analysis results (a LogAn output directory with a Parquet store and an HTML report) are written to a local
  directory the user chooses, with private permissions.
- The classifier model is cached locally in `~/.cache/logan-hf`.
- Nothing is stored anywhere else. Local files remain until the user deletes them.

## What is sent

- The plugin author collects no data: there is no telemetry, analytics or remote logging.
- Log content and analysis results are not sent anywhere by the skills. The container runs without network
  access (`--network none`) for analysis and for reading results.
- Network access happens only when the user agrees to the one-time download of the classifier model from
  huggingface.co (download only; HuggingFace telemetry is disabled), or when the user asks to install or pull
  LogAn from its upstream sources.
- The HTML report is served on 127.0.0.1 only.
- These third-party services, and the AI assistant you use the skills with, have their own privacy policies.

## Children

The plugin is a tool for engineers and is not intended for people under 18.

## Contact and changes

Questions and reports: [GitHub issues](https://github.com/biparasite/LogAn_skills/issues).
Changes to this policy are published in this file in the repository.
