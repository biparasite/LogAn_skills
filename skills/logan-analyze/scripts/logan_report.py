#!/usr/bin/env python3
"""
logan_report.py — compact summary and drill-down for a LogAn output directory,
so an agent does not have to read raw logs or the HTML report.

Sources (all inside the OUTPUT_DIR written by `logan analyze` / MCP `analyze_logs`):
  store/templates.parquet            templates: golden_signal, fault_category, occurrence_count, rep_log ...
  store/log_entries.parquet          entries: template_id, timestamp, file_source, original_text, variables
  developer_debug_files/store_templates.json   JSON copy of the templates (fallback without pyarrow)
  metrics/*.json                     preprocessing counters and timings

Dependencies: stdlib only; pyarrow is optional (needed for timestamps and entry-level drill-down).
The easiest way is to run it inside the LogAn image (logan_docker.sh report OUTPUT_DIR)
or with the Python of a local LogAn venv, which already has pyarrow.

Modes:
  logan_report.py OUT                       summary (markdown)
  logan_report.py OUT --format json         summary (JSON; stdout contains JSON only)
  logan_report.py OUT --template 42         template drill-down: entries over time + variable values
  logan_report.py OUT --around "2026-11-07 14:05" --window 10m   problem entries around a moment
  logan_report.py OUT --grep "timeout|refused"                  regex search over templates
Times are shown in the local time zone of the machine running the script (TZ inside the container);
add --utc to match LogAn's Log Explorer.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

try:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    HAS_ARROW = True
except Exception:  # noqa: BLE001
    HAS_ARROW = False

INFO_LABELS = {"info", "information"}
SIGNAL_ORDER = ["availability", "error", "saturation", "latency", "traffic", "information", "unknown"]
USE_UTC = False


# ---------------------------------------------------------------- helpers
def norm_signal(gs):
    if gs is None or (isinstance(gs, float) and gs != gs):
        return "unknown"
    s = str(gs).strip().lower()
    return "information" if s in INFO_LABELS else (s or "unknown")


def is_problem(gs):
    return norm_signal(gs) not in ("information", "unknown")


def fmt_ts(ts):
    if ts is None or ts == 0:
        return "—"
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc) if USE_UTC else datetime.fromtimestamp(int(ts))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return str(ts)


def tz_label():
    if USE_UTC:
        return "UTC"
    off = datetime.now().astimezone().strftime("%z")
    return f"local time (UTC{off[:3]}:{off[3:]})"


def parse_time(s):
    s = s.strip()
    if re.fullmatch(r"\d{9,11}", s):
        return int(s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            if USE_UTC:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    sys.exit(f"Cannot parse time {s!r}. Use 'YYYY-MM-DD HH:MM[:SS]' or epoch seconds.")


def parse_window(s):
    m = re.fullmatch(r"(\d+)\s*([smhd]?)", s.strip())
    if not m:
        sys.exit(f"Cannot parse window {s!r}. Examples: 30s, 10m, 2h, 1d")
    return int(m.group(1)) * {"": 60, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def clip(text, n):
    text = "" if text is None else str(text).replace("&#13;&#10;", " ").replace("\n", " ⏎ ")
    return text if len(text) <= n else text[: n - 1] + "…"


def md_cell(text, n=220):
    return clip(text, n).replace("|", "\\|")


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------- loading
def load_templates(out_dir):
    pq_path = os.path.join(out_dir, "store", "templates.parquet")
    js_path = os.path.join(out_dir, "developer_debug_files", "store_templates.json")
    rows, source = None, None
    if HAS_ARROW and os.path.isfile(pq_path):
        rows, source = pq.read_table(pq_path).to_pylist(), pq_path
    elif os.path.isfile(js_path):
        rows, source = load_json(js_path), js_path
    if rows is None:
        sys.exit(
            f"{out_dir} has neither store/templates.parquet nor developer_debug_files/store_templates.json.\n"
            "Is this a LogAn output directory, and did the run finish? (the run log ends with 'Analysis complete!')"
        )
    templates = {}
    for r in rows:
        try:
            tid = int(r.get("template_id"))
        except (TypeError, ValueError):
            continue
        fs = r.get("file_sources") or "{}"
        if isinstance(fs, str):
            try:
                fs = json.loads(fs)
            except json.JSONDecodeError:
                fs = {}
        templates[tid] = {
            "template_id": tid,
            "template": r.get("template") or "",
            "golden_signal": norm_signal(r.get("golden_signal")),
            "fault_category": r.get("fault_category") or "other",
            "occurrences": int(r.get("occurrence_count") or 0),
            "component": r.get("component"),
            "rep_log": r.get("rep_log") or "",
            "files": fs,
        }
    return templates, source


def load_entries(out_dir, columns):
    path = os.path.join(out_dir, "store", "log_entries.parquet")
    if not HAS_ARROW or not os.path.isfile(path):
        return None
    available = set(pq.read_schema(path).names)
    return pq.read_table(path, columns=[c for c in columns if c in available])


# ---------------------------------------------------------------- docker path map
PATH_MAP = []  # [(container_prefix, host_path, kind)] from logan_docker_inputs.tsv


def load_path_map(out_dir):
    """Map /data/in/N -> host path, written by logan_docker.sh for runs made through the wrapper."""
    PATH_MAP.clear()
    path = os.path.join(out_dir, "logan_docker_inputs.tsv")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 2 and parts[0].startswith("/data/in/"):
                    PATH_MAP.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    except OSError:
        pass
    PATH_MAP.sort(key=lambda m: -len(m[0]))


def host_path(p):
    """Translate a container path to the host path; return other paths unchanged."""
    if not p or not PATH_MAP:
        return p
    for prefix, host, kind in PATH_MAP:
        if p == prefix:
            return host
        if p.startswith(prefix + "/"):
            rest = p[len(prefix) + 1:]
            if kind == "file" or (not kind and rest == os.path.basename(host)):
                return host if rest == os.path.basename(host) else os.path.join(host, rest)
            return os.path.join(host, rest)
    return p


# ---------------------------------------------------------------- coverage
def read_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return [l.rstrip("\n") for l in fh if l.strip()]
    except OSError:
        return None


def count_lines(path):
    try:
        with open(path, "rb") as fh:
            return sum(1 for l in fh if l.strip())
    except OSError:
        return None


def raw_lines_in_store(out_dir):
    """Raw lines covered by stored entries. LogAn joins multi-line records (stack traces) with a newline."""
    path = os.path.join(out_dir, "store", "log_entries.parquet")
    if not HAS_ARROW or not os.path.isfile(path):
        return None
    pf = pq.ParquetFile(path)
    if "original_text" not in pf.schema_arrow.names:
        return None
    total = 0
    for batch in pf.iter_batches(columns=["original_text"], batch_size=65536):
        col = batch.column(0)
        nl = pc.sum(pc.count_substring(col, "\n")).as_py() or 0
        esc = pc.sum(pc.count_substring(col, "&#13;&#10;")).as_py() or 0
        total += batch.num_rows + nl + esc
    return total


def coverage(out_dir, store_records):
    m = load_json(os.path.join(out_dir, "metrics", "preprocessing.json")) or {}
    dbg = os.path.join(out_dir, "developer_debug_files")
    raw_total = m.get("num_log_lines_total")
    covered = raw_lines_in_store(out_dir)
    ignored = read_lines(os.path.join(dbg, "ignored_files.log"))
    processed = read_lines(os.path.join(dbg, "processed_files.log"))
    no_ts = count_lines(os.path.join(dbg, "none_logs.log"))
    skipped = pct = None
    if raw_total and covered is not None:
        skipped = max(raw_total - covered, 0)
        pct = round(100.0 * skipped / raw_total, 1)
    warnings = []
    if pct is not None and pct >= 5:
        warnings.append(f"LogAn did not keep ~{skipped} of {raw_total} input lines ({pct}%)")
    if ignored:
        warnings.append(f"ignored files: {len(ignored)} (their lines are not counted above)")
    return {
        "raw_lines_in_processed_files": raw_total,
        "records_after_preprocessing": m.get("num_log_lines_processed"),
        "records_in_store": store_records,
        "raw_lines_covered_by_store": covered,
        "raw_lines_skipped": skipped,
        "raw_lines_skipped_pct": pct,
        "lines_without_timestamp": no_ts,
        "files_processed": len(processed) if processed is not None else None,
        "files_ignored": [host_path(f) for f in ignored] if ignored is not None else None,
        "warnings": warnings,
    }


# ---------------------------------------------------------------- analysis
def time_stats(entries, templates):
    """first/last seen per template, global span, busiest minutes for problem lines."""
    if entries is None or entries.num_rows == 0:
        return None
    ts = entries.column("timestamp")
    valid = entries.filter(pc.greater(ts, 0))
    if valid.num_rows == 0:
        return None
    agg = valid.group_by("template_id").aggregate([("timestamp", "min"), ("timestamp", "max")])
    for tid, tmin, tmax in zip(agg.column("template_id").to_pylist(),
                               agg.column("timestamp_min").to_pylist(),
                               agg.column("timestamp_max").to_pylist()):
        if tid in templates:
            templates[tid]["first_seen"] = tmin
            templates[tid]["last_seen"] = tmax
    span = (pc.min(valid.column("timestamp")).as_py(), pc.max(valid.column("timestamp")).as_py())

    problem_ids = [t for t, v in templates.items() if is_problem(v["golden_signal"])]
    peaks = []
    if problem_ids:
        prob = valid.filter(pc.is_in(valid.column("template_id"), value_set=pa.array(problem_ids, pa.int64())))
        if prob.num_rows:
            minute = pc.multiply(pc.divide(prob.column("timestamp"), 60), 60)
            cnt = Counter(minute.to_pylist())
            peaks = cnt.most_common(8)
    return {"span": span, "peaks": peaks}


def build_summary(out_dir, top, rare_max):
    templates, source = load_templates(out_dir)
    entries = load_entries(out_dir, ["template_id", "timestamp", "file_source"])
    ts = time_stats(entries, templates)

    sig_templates, sig_lines, faults, comps, files = Counter(), Counter(), Counter(), Counter(), Counter()
    for t in templates.values():
        sig_templates[t["golden_signal"]] += 1
        sig_lines[t["golden_signal"]] += t["occurrences"]
        if is_problem(t["golden_signal"]):
            faults[t["fault_category"]] += t["occurrences"]
        if t.get("component"):
            comps[t["component"]] += t["occurrences"]
        for f, c in t["files"].items():
            files[f] += int(c)

    problems = [t for t in templates.values() if is_problem(t["golden_signal"])]
    by_count = sorted(problems, key=lambda t: -t["occurrences"])
    rare = sorted([t for t in problems if t["occurrences"] <= rare_max],
                  key=lambda t: (t.get("first_seen") or 0, t["occurrences"]))
    earliest = sorted([t for t in problems if t.get("first_seen")], key=lambda t: t["first_seen"])
    late_new = []
    if ts:
        lo, hi = ts["span"]
        cutoff = hi - (hi - lo) * 0.10
        late_new = sorted([t for t in problems if (t.get("first_seen") or 0) >= cutoff and hi > lo],
                          key=lambda t: t["first_seen"])

    total_lines = sum(t["occurrences"] for t in templates.values())
    cov = coverage(out_dir, entries.num_rows if entries is not None else total_lines)
    return {
        "output_dir": os.path.abspath(out_dir),
        "templates_source": source,
        "has_timestamps": bool(ts),
        "timezone": tz_label(),
        "totals": {
            "templates": len(templates),
            "lines": total_lines,
            "problem_templates": len(problems),
            "problem_lines": sum(t["occurrences"] for t in problems),
            "files": len(files),
        },
        "span": [fmt_ts(x) for x in ts["span"]] if ts else None,
        "signal_distribution": {s: {"templates": sig_templates[s], "lines": sig_lines[s]}
                                for s in SIGNAL_ORDER + sorted(set(sig_templates) - set(SIGNAL_ORDER))
                                if sig_templates[s]},
        "fault_distribution_problem_lines": dict(faults.most_common()),
        "component_distribution": dict(comps.most_common()) if comps else None,
        "coverage": cov,
        "files": {host_path(k): v for k, v in files.most_common()},
        "input_paths": [{"container": c, "host": h} for c, h, _ in sorted(PATH_MAP)] or None,
        "top_by_volume": [slim(t) for t in by_count[:top]],
        "rare_problems": [slim(t) for t in rare[:top]],
        "earliest_problems": [slim(t) for t in earliest[:top]],
        "new_in_last_10pct": [slim(t) for t in late_new[:top]],
        "busiest_problem_minutes": [{"minute": fmt_ts(m), "problem_lines": c} for m, c in ts["peaks"]] if ts else [],
        "metrics": {name: load_json(os.path.join(out_dir, "metrics", name))
                    for name in ("preprocessing.json", "anomaly.json")
                    if os.path.isfile(os.path.join(out_dir, "metrics", name))},
    }


def slim(t):
    return {
        "template_id": t["template_id"],
        "golden_signal": t["golden_signal"],
        "fault_category": t["fault_category"] if is_problem(t["golden_signal"]) else None,
        "occurrences": t["occurrences"],
        "component": t.get("component"),
        "first_seen": fmt_ts(t.get("first_seen")) if t.get("first_seen") else None,
        "last_seen": fmt_ts(t.get("last_seen")) if t.get("last_seen") else None,
        "files": sorted(host_path(f) for f in t["files"]),
        "template": clip(t["template"], 300),
        "example": clip(t["rep_log"], 300),
    }


# ---------------------------------------------------------------- render
def render_summary_md(s):
    o = []
    tt = s["totals"]
    o.append(f"# LogAn summary: {s['output_dir']}\n")
    o.append(f"- Template occurrences: **{tt['lines']}**, templates: **{tt['templates']}**, files: {tt['files']}")
    o.append(f"- Problem (non-information) templates: **{tt['problem_templates']}**, entries: {tt['problem_lines']}")
    if s["span"]:
        o.append(f"- Period: {s['span'][0]} → {s['span'][1]} ({s['timezone']})")
    elif not HAS_ARROW:
        o.append("- Timestamps unavailable: pyarrow is missing. Run `logan_docker.sh report OUT` instead")
    o.append("- Labels come from AI classification; verify them against raw lines.\n")

    c = s["coverage"]
    o.append("## Input coverage\n")
    if c["raw_lines_in_processed_files"] is not None:
        line = f"- Input lines in processed files: **{c['raw_lines_in_processed_files']}**"
        if c["raw_lines_covered_by_store"] is not None:
            line += f"; kept in the store: ~{c['raw_lines_covered_by_store']}; dropped: **~{c['raw_lines_skipped']} ({c['raw_lines_skipped_pct']}%)**"
        o.append(line)
    else:
        o.append("- No metrics/preprocessing.json: the number of input lines is unknown")
    o.append(f"- Entries in the store: {c['records_in_store']}" +
             (f" (after preprocessing: {c['records_after_preprocessing']})" if c["records_after_preprocessing"] is not None else "") +
             "; LogAn appends lines without a timestamp to the previous entry")
    if c["raw_lines_skipped"]:
        o.append("- Usual reasons: blank lines; lines where at least 65% of tokens are numbers; lines outside --time-range; "
                 "lines before the first timestamp of a file. Dropped lines are not analyzed, so errors in them are invisible here.")
    if c["lines_without_timestamp"]:
        o.append(f"- Lines without a timestamp: {c['lines_without_timestamp']} (see developer_debug_files/none_logs.log)")
    if c["files_processed"] is not None:
        o.append(f"- Files processed: {c['files_processed']}; ignored: {len(c['files_ignored'] or [])}")
    if c["files_ignored"]:
        for f in c["files_ignored"][:10]:
            o.append(f"  - ignored: `{f}`")
        if len(c["files_ignored"]) > 10:
            o.append(f"  - … {len(c['files_ignored']) - 10} more (developer_debug_files/ignored_files.log)")
        o.append("  - To include them: `--process-all-files` (or `--process-txt-files`), or pass the files explicitly with `-f`.")
    for w in c["warnings"]:
        o.append(f"- ⚠ {w}")
    o.append("")

    o.append("## Golden signals\n\n| signal | templates | entries |\n|---|---:|---:|")
    for k, v in s["signal_distribution"].items():
        o.append(f"| {k} | {v['templates']} | {v['lines']} |")
    if s["fault_distribution_problem_lines"]:
        o.append("\nFault categories (entries of problem templates): " +
                 ", ".join(f"{k}={v}" for k, v in s["fault_distribution_problem_lines"].items()))
    if s["component_distribution"]:
        o.append("\nCustom tags (entries): " + ", ".join(f"{k}={v}" for k, v in s["component_distribution"].items()))
    if len(s["files"]) > 1:
        o.append("\nFiles (entries): " + ", ".join(f"{os.path.basename(k)}={v}" for k, v in s["files"].items()))

    def table(title, rows, hint):
        if not rows:
            return
        o.append(f"\n## {title}\n\n_{hint}_\n")
        o.append("| id | signal | fault | count | first seen | last seen | example |")
        o.append("|---:|---|---|---:|---|---|---|")
        for r in rows:
            o.append(f"| {r['template_id']} | {r['golden_signal']} | {r['fault_category'] or '—'} | {r['occurrences']} | "
                     f"{r['first_seen'] or '—'} | {r['last_seen'] or '—'} | `{md_cell(r['example'] or r['template'])}` |")

    table("Earliest problems", s["earliest_problems"], "root-cause candidates: what broke first")
    table("New in the last 10% of the period", s["new_in_last_10pct"], "behavior that did not occur before")
    table("Rare problem templates", s["rare_problems"], "single events often matter more than bulk noise")
    table("Highest-volume problem templates", s["top_by_volume"], "volume is not importance; often a consequence")
    if s["busiest_problem_minutes"]:
        o.append("\n## Minutes with the most problem entries\n")
        o.append(", ".join(f"{p['minute']} ({p['problem_lines']})" for p in s["busiest_problem_minutes"]))
    if s.get("input_paths"):
        o.append("\n## Input paths (container → host)\n")
        o.extend(f"- `{m['container']}` → `{m['host']}`" for m in s["input_paths"])
    o.append("\nNext: `--template ID` for details, `--around \"<time>\" --window 10m` for correlation, "
             "`--grep REGEX` to search templates.")
    return "\n".join(o)


def cmd_template(out_dir, tid, limit):
    templates, _ = load_templates(out_dir)
    t = templates.get(tid)
    if not t:
        sys.exit(f"No template {tid}. Known ids: {sorted(templates)[:30]}…")
    o = [f"# Template {tid}: {t['golden_signal']}"
         + (f" / {t['fault_category']}" if is_problem(t["golden_signal"]) else ""),
         f"- Occurrences: {t['occurrences']}; files: {json.dumps({host_path(k): v for k, v in t['files'].items()}, ensure_ascii=False)}",
         f"- Tag: {t['component']}" if t.get("component") else "",
         f"- Template: `{clip(t['template'], 500)}`",
         f"- Example: `{clip(t['rep_log'], 500)}`"]
    entries = load_entries(out_dir, ["template_id", "timestamp", "file_source", "original_text", "variables", "entry_id"])
    if entries is None:
        o.append("\n(entries unavailable: pyarrow and store/log_entries.parquet are required)")
        return "\n".join(x for x in o if x)
    sel = entries.filter(pc.equal(entries.column("template_id"), tid))
    sort_keys = [("timestamp", "ascending")] + ([("entry_id", "ascending")] if "entry_id" in sel.column_names else [])
    sel = sel.sort_by(sort_keys)
    rows = sel.to_pylist()
    var_counts = {}
    for r in rows:
        try:
            vals = json.loads(r.get("variables") or "[]")
        except json.JSONDecodeError:
            vals = []
        for i, v in enumerate(vals):
            var_counts.setdefault(i, Counter())[v] += 1
    if var_counts:
        o.append("\n## Variable values (<*> by position, top 5)")
        for i in sorted(var_counts):
            c = var_counts[i]
            o.append(f"- var{i} ({len(c)} distinct): " + ", ".join(f"`{clip(k, 60)}`×{n}" for k, n in c.most_common(5)))
    shown = rows if len(rows) <= limit else rows[: limit // 2] + [None] + rows[-(limit - limit // 2):]
    o.append(f"\n## Entries ({len(rows)} total, by time, {tz_label()})")
    for r in shown:
        if r is None:
            o.append(f"  … {len(rows) - limit} omitted …")
            continue
        o.append(f"- {fmt_ts(r.get('timestamp'))} [{os.path.basename(r.get('file_source') or '')}] "
                 f"{clip(r.get('original_text') or '', 400)}")
    return "\n".join(x for x in o if x)


def cmd_around(out_dir, center, window, limit, include_info):
    templates, _ = load_templates(out_dir)
    entries = load_entries(out_dir, ["template_id", "timestamp", "file_source", "original_text", "entry_id"])
    if entries is None:
        sys.exit("pyarrow and store/log_entries.parquet are required (run: logan_docker.sh report OUT)")
    lo, hi = center - window, center + window
    ts = entries.column("timestamp")
    sel = entries.filter(pc.and_(pc.greater_equal(ts, lo), pc.less_equal(ts, hi)))
    if not include_info:
        ids = [t for t, v in templates.items() if is_problem(v["golden_signal"])]
        sel = sel.filter(pc.is_in(sel.column("template_id"), value_set=pa.array(ids, pa.int64())))
    sort_keys = [("timestamp", "ascending")] + ([("entry_id", "ascending")] if "entry_id" in sel.column_names else [])
    rows = sel.sort_by(sort_keys).to_pylist()
    o = [f"# {'All' if include_info else 'Problem'} entries {fmt_ts(lo)} … {fmt_ts(hi)} ({tz_label()}): {len(rows)}"]
    per_tpl = Counter(r["template_id"] for r in rows)
    if per_tpl:
        o.append("Templates in the window: " + ", ".join(
            f"{tid}({templates.get(tid, {}).get('golden_signal', '?')})×{n}" for tid, n in per_tpl.most_common(15)))
    for r in rows[:limit]:
        t = templates.get(r["template_id"], {})
        o.append(f"- {fmt_ts(r['timestamp'])} [{os.path.basename(r.get('file_source') or '')}] "
                 f"#{r['template_id']} {t.get('golden_signal', '?')}: {clip(r.get('original_text') or '', 300)}")
    if len(rows) > limit:
        o.append(f"… {len(rows) - limit} more. Narrow --window or raise --limit.")
    return "\n".join(o)


def cmd_grep(out_dir, pattern, include_info):
    templates, _ = load_templates(out_dir)
    rx = re.compile(pattern, re.IGNORECASE)
    hits = [t for t in templates.values()
            if (include_info or is_problem(t["golden_signal"])) and (rx.search(t["template"]) or rx.search(t["rep_log"]))]
    hits.sort(key=lambda t: -t["occurrences"])
    o = [f"# Templates matching /{pattern}/: {len(hits)}" + ("" if include_info else " (information excluded; add --include-info to see all)")]
    for t in hits[:50]:
        o.append(f"- #{t['template_id']} {t['golden_signal']}/{t['fault_category']} ×{t['occurrences']}: "
                 f"`{clip(t['rep_log'] or t['template'], 250)}`")
    return "\n".join(o)


def main():
    global USE_UTC
    ap = argparse.ArgumentParser(description="Summary and drill-down for LogAn output")
    ap.add_argument("output_dir")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--top", type=int, default=12, help="rows per summary table")
    ap.add_argument("--rare-max", type=int, default=5, help="max occurrences for a 'rare' template")
    ap.add_argument("--template", type=int, help="drill down into a template_id")
    ap.add_argument("--around", help="moment: 'YYYY-MM-DD HH:MM[:SS]' or epoch seconds")
    ap.add_argument("--window", default="10m", help="half-width of the --around window (30s/10m/2h)")
    ap.add_argument("--grep", help="regex over templates and example lines")
    ap.add_argument("--include-info", action="store_true", help="include information templates")
    ap.add_argument("--limit", type=int, default=40, help="max entries in drill-down output")
    ap.add_argument("--utc", action="store_true", help="show times in UTC (as Log Explorer does)")
    a = ap.parse_args()
    USE_UTC = a.utc
    if a.limit < 1 or a.top < 1 or a.rare_max < 0:
        ap.error("--limit and --top must be positive, --rare-max nonnegative")
    if not os.path.isdir(a.output_dir):
        sys.exit(f"No such directory: {a.output_dir}")
    load_path_map(a.output_dir)

    if a.template is not None:
        print(cmd_template(a.output_dir, a.template, a.limit))
    elif a.around:
        print(cmd_around(a.output_dir, parse_time(a.around), parse_window(a.window), a.limit, a.include_info))
    elif a.grep:
        print(cmd_grep(a.output_dir, a.grep, a.include_info))
    else:
        s = build_summary(a.output_dir, a.top, a.rare_max)
        print(json.dumps(s, ensure_ascii=False, indent=2) if a.format == "json" else render_summary_md(s))


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:  # output piped into head/less and closed early
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)
