# Investigation playbook for LogAn output

## Order of work

1. **Summary and coverage.** Start with the coverage and template queries in `queries.md` and note:
   - the period covered and the share of problem entries;
   - the "Input coverage" block: how many input lines LogAn dropped and which files it ignored (e.g.
     `syslog`, `*.log.1`). A large gap limits every conclusion, so mention it in the report or rerun with
     `--process-all-files` / `-t all-data`;
   - how the data relates to what was asked: host, service, stream and time window. Counts from a capped
     export apply only to that export.
2. **Background vs incident.** For each notable problem template compare `first seen` / `last seen` with the
   period. A template that runs from the first to the last minute at a roughly steady rate is chronic
   background, not the cause of today's problem: mention it, do not conclude from it. If unsure, look at the
   template's entries over time and their density.
3. **Root-cause candidates**, in order of value:
   - **earliest problems**, especially those that started **before** the symptom the user reported;
   - **rare** templates (1–5 occurrences) with `availability`/`error` and fault `device`/`io`: disk errors,
     OOM, kernel panic, certificate failures;
   - **new in the last 10%** of the period: behavior that was not there before;
   - **peak minutes**: what exactly jumped at that moment.
4. **Window around the symptom.** Query problem entries ±10 minutes around the symptom, then ±30 minutes.
   Look for the first unusual event before the burst, including `information` templates (restarts, deploys,
   config changes, failover).
5. **Drill down per candidate.** The template's entries and variable values show:
   - the values of `<*>`: one host, user or endpoint versus "all of them" changes the conclusion;
   - first and last occurrence;
   - the files the template appears in.
6. **Correlate across files and sources.** Build a timeline such as "14:20 kern.log ext4 error on sda1 →
   15:31 app.log DB pool exhausted → 15:33 5xx on /api/v1/pay", each link backed by a line. Look for
   independent evidence: monitoring events, metrics, deploy records.
7. **Try to refute.** Do the same errors occur during a quiet period? Are timestamp-less lines (placed at the
   end of the period) distorting "before"? Does the raw line contradict the label? Where a label is doubtful,
   rerun into a new directory with `-m bart` and compare.
8. **Gaps.** List what is missing for a confident conclusion: logs of another service, metrics, other hosts,
   an earlier period.

## Heuristics

| Observation | Most likely |
|---|---|
| `saturation` (pools, queues, limits) shortly before bulk `latency` and `error` | Resource exhaustion → timeouts → 5xx. Look for what grew first: traffic, a slow query, a leak |
| `latency`/`network` with the same address in the variables | A specific dependency or host is in trouble |
| `authentication` for many users at once | Broken auth service, expired certificate or key, not brute force |
| `authentication` for one user or one IP, repeatedly | Password guessing or wrong service credentials |
| `device`/`io`, even a single occurrence | Hardware or filesystem problem; often the root cause and easy to miss |
| Bulk `error` with no `saturation`/`availability` before it | A code change or release. Look for start/deploy lines (usually `information`) next to the first error |
| A sudden rise of `traffic` | Load spike, retry storm, bot |

`information` templates can matter too: restarts, deploys, config changes, failover. Before the final
conclusion check the window around the incident start with `information` templates included.

## Report template

```markdown
## Summary
<1–3 sentences: what happened, the likely root cause, confidence: high/medium/low>

## Timeline
| Time (zone) | Source | Event | Template |
|---|---|---|---|
| 14:20:16 | kern.log | EXT4-fs error on sda1 (2×) | #3 availability/io |
| ... | | | |

## Evidence
- Hypothesis 1: <...>
  - `<raw line>` (#id, file, time)
  - Why I believe it: <...>. What would refute it: <...>
- Rejected: <hypothesis>, because <...>

## Background (unrelated to the incident)
- #id <template>: present over the whole period at a steady rate

## Not covered
- LogAn dropped ~N of M input lines (P%); ignored files: <...>
- <lines without timestamps, missing logs of service X>

## Next steps
1. <a concrete check or action>

_Golden signal/fault labels come from LogAn's AI classification; the conclusions above were checked against raw lines._
Log Explorer: serve <out> on 127.0.0.1 → http://localhost:8000/log_diagnosis/explorer.html
```

Keep the report short. Quote raw lines sparingly (1–3 per claim), redacted where they contain secrets or
personal data.
