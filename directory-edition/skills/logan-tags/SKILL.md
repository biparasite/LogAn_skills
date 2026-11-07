---
name: logan-tags
description: Design, check and test LogAn custom tag configs (--tag-config / LOGAN_TAG_CONFIG) that group log templates by component, subsystem or team (Auth, Storage, Network, Database, your services). Use when the user wants LogAn results categorized by component, asks for a LogAn tag config, or wants to investigate untagged or ambiguous log patterns.
---

# Custom tags for LogAn

Tags add a `component` field to every template. It appears in `templates.parquet`, in the Log Explorer filter
and in the tag query of the `logan-analyze` skill. Tags work through the CLI or the container
(`--tag-config`, `LOGAN_TAG_CONFIG`); MCP `analyze_logs` cannot pass them. Answer in the user's language.
`<skill-dir>` below is the directory of this skill; replace each `<…>` placeholder with a real value.

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

1. **Explore.** Count the bracket tokens and process names in a log file (shown for one file; repeat per file):
   ```bash
   grep -ohE '\[[^][]+\]|[A-Za-z][A-Za-z0-9._-]+\[[0-9]+\]' <log file> | sed -E 's/\[[0-9]+\]//; s/[][]//g' | tr 'A-Z' 'a-z' | grep '[a-z]' | sort | uniq -c | sort -rn | head -40
   ```
   With few tokens (JSON logs, `kernel:` lines, device messages, custom formats) build the rules on `patterns`.
2. **Draft.** Start from a copy of `<skill-dir>/assets/starter_tags.json` (Auth, Storage, Kernel/Memory,
   Network, Containers, Database, Web, Systemd/Services) or write one for the user's system; ask which
   components matter if that is not obvious. Keep the config next to the user's project, e.g. `./logan-tags.json`.
3. **Check every rule before running:**
   - every tag has a non-empty `name`, and `keywords`/`patterns` are arrays of strings;
   - keywords are lower case and at least 3 characters long (shorter substrings cause false matches);
   - every pattern is a valid regular expression and cannot match an empty string (`x*` matches every line);
   - specific tags come **before** generic ones, because the first tag wins ties;
   - no tag name is declared twice, and the only top-level keys are `tags` and `default_tag`.
4. **Test on a sample.** Run LogAn with the config on a representative, non-sensitive sample into a new output
   directory (the container command from the `logan-analyze` skill, plus these two parts):
   ```bash
   -v <config>:/data/config/tags.json:ro          # extra mount
   --tag-config /data/config/tags.json            # extra analyze argument
   ```
   LogAn prints `Tagging complete. Tags found: [...]`. Then run the custom-tags query from
   `logan-analyze/references/queries.md` and look at:
   - entries per tag and the share of `default_tag` (often reasonable under 20–30% of entries);
   - tags that never matched;
   - the largest `default_tag` templates (`rep_log`), which are the sources for new rules;
   - a few `rep_log` examples per tag, to catch false positives.
5. **Iterate** on the config and rerun into a new directory until the split makes sense, then use it for the
   real analysis.
