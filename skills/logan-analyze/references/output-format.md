# LogAn output format and run details

Checked against the LogAn source (`logan/cli.py`, `logan/mcp/server.py`, `logan/store/store.py`,
`logan/log_diagnosis/*`, `logan/preprocessing/preprocessing.py`): branch `main`, commit `70d4975` of
2026-07-30, package version 0.0.1.

## Output directory

```
OUTPUT_DIR/
├── log_diagnosis/
│   ├── explorer.html            # interactive Log Explorer (serve over HTTP, not file://)
│   └── libs/                    # JS libraries and DuckDB-WASM
├── store/
│   ├── templates.parquet        # one row per unique template
│   └── log_entries.parquet      # one row per stored entry
├── developer_debug_files/       # written in debug mode (on by default)
│   ├── store_templates.json     # JSON copy of templates.parquet (always written)
│   ├── temp_id_to_signal_map.json   # "('tid', 'file')" -> [golden_signal, [[fault]]]  (the MCP response is built from it)
│   ├── temp_id_to_rep_log.json
│   ├── processed_files.log      # files actually read
│   ├── ignored_files.log        # files skipped (name rules, .csv/.xml/.tsv, extraction errors)
│   ├── none_logs.log            # lines without a timestamp (debug mode only)
│   └── golden_signal_timeline.json  # {files, by_file: {file: {bin_seconds, bin_label, signals, bins:[{start,label,<signal>:n}]}}}
├── metrics/
│   ├── preprocessing.json       # num_log_lines_total (input lines), num_log_lines_processed (entries), timing
│   └── anomaly.json             # {"anomaly_detection_time_ms": ...}
├── run/ test_templates/ pandarallel_cache/   # internal
└── logan_docker_inputs.tsv      # only for runs through logan_docker.sh: /data/in/N <TAB> host path <TAB> file|dir
```

## templates.parquet

| Column | Type | Meaning |
|---|---|---|
| `template_id` | int | Drain3 template id (same as `log_entries.template_id`) |
| `template` | str | template with `<*>` for variables |
| `var_count` | int | number of `<*>` |
| `golden_signal` | str | see Labels; voted when a template spans several files |
| `fault_category` | str | see Labels |
| `occurrence_count` | int | entries in the template |
| `component` | str/null | custom tag (only with `--tag-config`) |
| `rep_log` | str | example raw line |
| `file_sources` | str (JSON) | `{"path": entries}` |

## log_entries.parquet

| Column | Type | Meaning |
|---|---|---|
| `entry_id` | int | position before sorting; preserves the original order within a file |
| `template_id` | int | template reference (-1 if unrecognized) |
| `timestamp` | int64 | epoch seconds; 0 = none. LogAn gives entries without a timestamp the latest epoch in the data |
| `file_source` | str | file path (inside the container for Docker runs: `/data/in/N/...`) |
| `original_text` | str | raw text; multi-line entries (stack traces) are joined with newlines |
| `variables` | str (JSON) | values of `<*>` in order |

Entries are sorted by time, newest first. For ad-hoc queries DuckDB is handy if installed:
`duckdb -c "SELECT template_id, count(*) FROM 'OUT/store/log_entries.parquet' GROUP BY 1 ORDER BY 2 DESC LIMIT 20"`.

## Coverage counters

- `metrics/preprocessing.json` → `num_log_lines_total` counts raw lines in the processed files;
  `num_log_lines_processed` counts entries after preprocessing (multi-line records are already joined).
- `logan_report.py` adds `coverage.raw_lines_covered_by_store` (entries plus the extra lines joined into them)
  and `raw_lines_skipped` = input lines − covered lines. Lines of ignored files are not part of either count;
  they are listed in `coverage.files_ignored`.

## Labels

- **golden_signal**: `information`, `error`, `availability`, `latency`, `saturation`, `traffic`.
  `Info` is the default for templates that could not be classified; treat it as `information`.
- **fault_category**: `io`, `authentication`, `network`, `application`, `device`, `other`; meaningful only for
  non-information templates.
- Classification is zero-shot (an NLI model compares the line with the label names). Watch for lines that
  contain the word `error` in a field name (`errors=0`), `timeout` in configuration lines, and multi-line
  traces labeled by their first line. Verify every label against raw lines.

## MCP responses

`extract_templates` →
```json
{"status":"success","total_log_lines":2000,"templates_found":118,"output_dir":"...",
 "templates":[{"template_id":"1","template":"... <*> ...","occurrences":523}, ...]}
```
These ids come from a separate Drain3 pass and may differ from `analyze_logs` and the Parquet store.

`analyze_logs` →
```json
{"status":"success","total_log_lines":2000,"templates_found":118,
 "golden_signal_distribution":{"information":1700,"error":180,...},
 "output_dir":"...",
 "results":[{"template_id":"7","log_line":"...","golden_signal":"error",
             "fault_categories":"network","occurrences":95,"file_name":"..."}, ...]}
```
Errors come back as `{"status":"error","message":"..."}` (e.g. `File not found`, "No log lines could be
extracted"). `results` can be long; summarize `output_dir` with `logan_report.py` instead of restating it.

## Docker without the wrapper

The image entrypoint `run.sh` reads environment variables:
```bash
docker run --rm --network none --user <uid>:<gid> --group-add 0 \
  -v /abs/logs/:/data/input/:ro -v /abs/new-out/:/data/output/ -v ~/.cache/logan-hf:/tmp/hf_cache \
  -e HF_HUB_OFFLINE=1 -e LOGAN_INPUT_FILES="/data/input/" -e LOGAN_OUTPUT_DIR=/data/output/ \
  -e LOGAN_TIME_RANGE=all-data -e LOGAN_PROCESS_ALL_FILES=true \
  ghcr.io/log-analyzer/logan:latest
```
(`<uid>:<gid>` are your `id -u` and `id -g`; drop `--network none` and `HF_HUB_OFFLINE` for the one-time model
download; rootless podman uses `--userns=keep-id` instead of `--group-add 0`).

| Variable | Default | Values |
|---|---|---|
| `LOGAN_INPUT_FILES` | — | container paths, comma-separated |
| `LOGAN_INPUT_GLOB` | — | glob inside the container |
| `LOGAN_OUTPUT_DIR` | `/data/output` | |
| `LOGAN_TIME_RANGE` | `all-data` | see SKILL.md |
| `LOGAN_MODEL_TYPE` | `zero_shot` | `zero_shot`, `custom` |
| `LOGAN_MODEL` | `crossencoder` | `crossencoder`, `bart`, HF model name, `path.py:Class` |
| `LOGAN_DEBUG_MODE` | `true` | |
| `LOGAN_PROCESS_ALL_FILES` | `false` | |
| `LOGAN_PROCESS_LOG_FILES` | `true` | |
| `LOGAN_PROCESS_TXT_FILES` | `false` | |
| `LOGAN_CLEAN_UP` | `false` | |
| `LOGAN_TAG_CONFIG` | — | JSON path inside the container |

`LOGAN_MODE=view` is marked as broken by the authors (TODO in the Containerfile); use `logan_docker.sh view`.

The wrapper calls the CLI directly:
`docker run --rm ... --entrypoint /opt/app-root/src/.venv/bin/logan IMAGE analyze -f /data/in/1 -o /data/output`.
The image's Python (`/opt/app-root/src/.venv/bin/python`) has pandas and pyarrow. By default the container runs
as UID 1001, GID 0; DuckDB-WASM is bundled in the image, so the explorer is built without network access.
