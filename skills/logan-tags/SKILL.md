---
name: logan-tags
description: Design, validate and test LogAn custom tag configs (--tag-config / LOGAN_TAG_CONFIG) that group log templates by component, subsystem or team (Auth, Storage, Network, Database, your services). Use when the user wants LogAn results categorized by component, asks for a LogAn tag config, or wants to investigate untagged or ambiguous log patterns.
---

# Custom tags for LogAn

Tags add a `component` field to every template. It appears in `templates.parquet`, in the Log Explorer filter
and in the `logan_report.py` summary. Tags work through the CLI or Docker (`--tag-config`, `LOGAN_TAG_CONFIG`);
MCP `analyze_logs` cannot pass them. Answer in the user's language.

`<skill-dir>` below is the directory of this skill. `scripts/tag_tool.py` uses only the Python standard
library and checks a config without running LogAn; its tokenization and scoring match LogAn's code.

## How LogAn assigns a tag

You need this for rules that actually work:

1. A tag is assigned **to the whole template** from its **first** line, then applied to all its lines.
2. **Bracket tokens** are extracted in lower case: the contents of `[...]` (except numbers and timestamps)
   and the process name before `[PID]` (`sshd[123]` → `sshd`). Lines like `kernel: ...` or `app: ...`
   have no tokens, so **keywords never match them**; use `patterns`.
3. Scoring: +1 for each token that contains at least one keyword as a **substring**; +1 if at least one
   regex in `patterns` matches the full line (case-insensitive). Several matching patterns still add only 1.
4. The highest score wins. On a tie the tag **listed first** wins. Zero points gives `default_tag`
   (default `Other`).
5. Keywords with uppercase letters never match (tokens are lowercased). LogAn silently skips invalid regexes.

Format:
```json
{ "tags": [ { "name": "Auth", "keywords": ["sshd", "sudo"], "patterns": ["authentication failure"] } ],
  "default_tag": "Other" }
```

## Workflow

1. **Explore.** See which tokens the logs contain:
   ```bash
   python3 <skill-dir>/scripts/tag_tool.py tokens LOG_PATHS --top 40 --max-lines 100000
   ```
   With few tokens (JSON logs, `kernel:` lines, device messages, custom formats) build the rules on `patterns`.
2. **Draft.** Start from a copy of `<skill-dir>/assets/starter_tags.json` (Auth, Storage, Kernel/Memory,
   Network, Containers, Database, Web, Systemd/Services) or write one for the user's system; ask which
   components matter if that is not obvious.
   - Put specific tags **above** generic ones: the first tag wins ties.
   - Write keywords in lower case, at least 3 characters long; shorter substrings cause false matches.
   - Use `\b` and concrete phrases in patterns rather than single common words.
   - Keep the config next to the user's project, e.g. `./logan-tags.json`, not inside this skill.
3. **Test and iterate:**
   ```bash
   python3 <skill-dir>/scripts/tag_tool.py test ./logan-tags.json LOG_PATHS
   ```
   The output shows coverage per tag (templates and lines), tags that never matched, examples per tag
   (to spot false positives), ties between tags, and the top lines that fell into `default_tag` (sources
   for new rules). Iterate until `default_tag` is reasonable (often under 20–30% of lines) and the
   examples make sense. `ERROR` means the rule will not work in LogAn. Templates here are approximated, and
   the test only covers the sampled input, so LogAn's numbers differ slightly.
4. **Run the analysis with tags** (see the `logan-analyze` skill), into a new output directory:
   ```bash
   bash <skill-dir>/../logan-analyze/scripts/logan_docker.sh analyze -f LOGS --tag-config ./logan-tags.json -o ./logan-runs/tagged
   # local install: logan analyze -f LOGS -o OUT --tag-config ./logan-tags.json
   ```
   LogAn prints `Tagging complete. Tags found: [...]`; the report then shows "Custom tags (entries)" and JSON
   `component_distribution`.

`python3 <skill-dir>/scripts/tag_tool.py validate ./logan-tags.json` checks only the schema and regexes,
e.g. before committing a config.
