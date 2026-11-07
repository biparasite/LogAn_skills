#!/usr/bin/env python3
"""
tag_tool.py — draft and check LogAn custom tag configs (--tag-config / LOGAN_TAG_CONFIG)
without running LogAn. Token extraction and scoring replicate
logan/idm_component_tagger/{bracket_extractor,tagger}.py from LogAn
(https://github.com/Log-Analyzer/LogAn, Apache License 2.0, IBM Research / Red Hat).

  tag_tool.py tokens   LOGS...               frequent bracket tokens (keyword candidates)
  tag_tool.py validate CONFIG.json          schema and regex check
  tag_tool.py test     CONFIG.json LOGS...  coverage: templates/lines per tag, what falls
                                            into default_tag, and ties between tags

LOGS are files or directories (recursive; .gz is read). Standard library only.
Note: LogAn tags a whole template by its FIRST line. Templates are approximated here by
masking numbers and addresses, so coverage numbers are close to LogAn's but not identical.
"""
import argparse
import gzip
import json
import os
import re
import sys
from collections import Counter, defaultdict

# --- same logic as logan/idm_component_tagger/bracket_extractor.py (LogAn, Apache-2.0)
_BRACKET_RE = re.compile(r'\[([^\[\]]+)\]')
_PROCESS_PID_RE = re.compile(r'(\b[a-zA-Z][\w._-]+)\[\d+\]')
_NUMERIC_RE = re.compile(r'^\d+$')
_TIMESTAMP_RE = re.compile(r'^\d{4}-\d{2}-\d{2}|^\d{2}:\d{2}:\d{2}|^\w{3}\s+\d{1,2}')


def extract_bracket_tokens(log_line):
    tokens = []
    for tok in _BRACKET_RE.findall(log_line):
        tok = tok.strip()
        if len(tok) < 2 or _NUMERIC_RE.match(tok) or _TIMESTAMP_RE.match(tok):
            continue
        tokens.append(tok.lower())
    for tok in _PROCESS_PID_RE.findall(log_line):
        tok = tok.strip().lower()
        if len(tok) >= 2 and tok not in tokens:
            tokens.append(tok)
    return tokens


# --- same logic as match_tag in logan/idm_component_tagger/tagger.py (LogAn, Apache-2.0), plus scores for diagnostics
def match_tag(tokens, log_line, tags, default_tag):
    best_tag, best_score, scores = default_tag, 0, {}
    for tag in tags:
        score = 0
        for tok in tokens:
            for kw in tag.get("keywords", []):
                if kw in tok:
                    score += 1
                    break
        for pat in tag.get("patterns", []):
            try:
                if re.search(pat, log_line, re.IGNORECASE):
                    score += 1
                    break
            except re.error:
                continue
        scores[tag["name"]] = score
        if score > best_score:
            best_score, best_tag = score, tag["name"]
    return best_tag, best_score, scores


# --- approximation of Drain3 templates
_MASKS = [
    (re.compile(r'^\S+\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+'), ''),                    # syslog prefix "Nov  7 14:00:00 "
    (re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?'), '<TS>'),
    (re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}\b'), '<UUID>'),
    (re.compile(r'\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b'), '<IP>'),
    (re.compile(r'\b0x[0-9a-fA-F]+\b|\b[0-9a-fA-F]{12,}\b'), '<HEX>'),
    (re.compile(r'\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b'), '<D>'),
    (re.compile(r'\d+'), '<N>'),
]


def pseudo_template(line):
    for rx, rep in _MASKS:
        line = rx.sub(rep, line)
    return line.strip()


def iter_lines(paths, max_lines):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in sorted(names)]
        elif os.path.isfile(p):
            files.append(p)
        else:
            print(f"! no such path: {p}", file=sys.stderr)
    n = 0
    for f in files:
        opener = gzip.open if f.endswith(".gz") else open
        try:
            with opener(f, "rt", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if not line.strip():
                        continue
                    yield f, line
                    n += 1
                    if n >= max_lines:
                        print(f"(read the first {max_lines} lines; change with --max-lines)", file=sys.stderr)
                        return
        except (OSError, EOFError) as e:
            print(f"! cannot read {f}: {e}", file=sys.stderr)


def clip(s, n=160):
    return s if len(s) <= n else s[: n - 1] + "…"


# --- validate
def validate(path):
    errors, warns = [], []
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        return None, [f"not valid JSON: {e}"], []
    if not isinstance(cfg, dict) or not isinstance(cfg.get("tags"), list) or not cfg["tags"]:
        return None, ["expected an object with a non-empty \"tags\" array"], []
    if "default_tag" in cfg and (not isinstance(cfg["default_tag"], str) or not cfg["default_tag"].strip()):
        errors.append("default_tag must be a nonempty string")
    names = Counter()
    for i, t in enumerate(cfg["tags"]):
        where = f"tags[{i}]"
        if not isinstance(t, dict) or not isinstance(t.get("name"), str) or not t["name"].strip():
            errors.append(f"{where}: missing \"name\" (LogAn fails with KeyError)")
            continue
        where = f"tag '{t['name']}'"
        names[t["name"]] += 1
        kws, pats = t.get("keywords", []), t.get("patterns", [])
        if not isinstance(kws, list) or not isinstance(pats, list):
            errors.append(f"{where}: keywords/patterns must be arrays of strings")
            continue
        if not kws and not pats:
            warns.append(f"{where}: neither keywords nor patterns, the tag can never match")
        for kw in kws:
            if not isinstance(kw, str) or not kw:
                errors.append(f"{where}: empty or non-string keyword {kw!r}")
            elif kw != kw.lower():
                errors.append(f"{where}: keyword {kw!r} has uppercase letters; tokens are lowercased, so it NEVER matches")
            elif len(kw) < 3:
                warns.append(f"{where}: keyword {kw!r} is shorter than 3 characters; substring matching will cause false positives")
        for p in pats:
            if not isinstance(p, str):
                errors.append(f"{where}: expected a string pattern, got {p!r}")
                continue
            try:
                re.compile(p)
            except (re.error, TypeError) as e:
                errors.append(f"{where}: invalid regex {p!r}: {e} (LogAn silently skips it)")
            else:
                if re.search(p, "", re.IGNORECASE) is not None:
                    errors.append(f"{where}: regex {p!r} matches the empty string, so it matches every line")
    for n, c in names.items():
        if c > 1:
            warns.append(f"tag '{n}' is declared {c} times; on equal scores the first one in the list wins")
    for k in cfg:
        if k not in ("tags", "default_tag"):
            warns.append(f"unknown top-level key {k!r}; LogAn ignores it")
    return cfg, errors, warns


def cmd_validate(a):
    cfg, errors, warns = validate(a.config)
    for w in warns:
        print("WARN ", w)
    for e in errors:
        print("ERROR", e)
    if cfg and not errors:
        print(f"OK: {len(cfg['tags'])} tags, default_tag={cfg.get('default_tag', 'Other')!r}")
    return 1 if errors or not cfg else 0


def cmd_tokens(a):
    cnt, example = Counter(), {}
    lines = 0
    for _, line in iter_lines(a.logs, a.max_lines):
        lines += 1
        for t in set(extract_bracket_tokens(line)):
            cnt[t] += 1
            example.setdefault(t, line)
    print(f"Lines: {lines}; distinct bracket tokens: {len(cnt)}")
    if not cnt:
        print("No tokens: the logs contain no [..] or process[PID]. Tag with patterns (regex over the whole line).")
        return 0
    print(f"\n{'lines':>8}  token  —  example")
    for t, c in cnt.most_common(a.top):
        print(f"{c:>8}  {t}  —  {clip(example[t], 120)}")
    return 0


def cmd_test(a):
    cfg, errors, warns = validate(a.config)
    for w in warns:
        print("WARN ", w)
    for e in errors:
        print("ERROR", e)
    if errors or not cfg:
        return 1
    tags = [{"name": t["name"], "keywords": t.get("keywords", []), "patterns": t.get("patterns", [])}
            for t in cfg["tags"] if isinstance(t, dict) and t.get("name")]
    default = cfg.get("default_tag", "Other")

    groups = {}  # pseudo_template -> [first_line, count]
    for _, line in iter_lines(a.logs, a.max_lines):
        k = pseudo_template(line)
        if k in groups:
            groups[k][1] += 1
        else:
            groups[k] = [line, 1]
    if not groups:
        print("No lines to test.")
        return 1

    per_tag_tpl, per_tag_lines = Counter(), Counter()
    untagged, ties, examples = [], [], defaultdict(list)
    for k, (first, n) in groups.items():
        tag, score, scores = match_tag(extract_bracket_tokens(first), first, tags, default)
        per_tag_tpl[tag] += 1
        per_tag_lines[tag] += n
        if tag == default:
            untagged.append((n, first))
        else:
            if len(examples[tag]) < 3:
                examples[tag].append(first)
            top = [t for t, s in scores.items() if s == score]
            if len(top) > 1:
                ties.append((n, first, top))

    total_l, total_t = sum(per_tag_lines.values()), len(groups)
    print(f"\nApprox. templates: {total_t}, lines: {total_l}\n")
    print(f"{'tag':<24}{'templates':>10}{'lines':>10}{'% lines':>9}")
    for t in [x["name"] for x in tags] + [default]:
        if per_tag_tpl[t] or t == default:
            print(f"{t:<24}{per_tag_tpl[t]:>10}{per_tag_lines[t]:>10}{100 * per_tag_lines[t] / total_l:>8.1f}%")
    never = [x["name"] for x in tags if not per_tag_tpl[x["name"]]]
    if never:
        print(f"\nNever matched: {', '.join(never)}")
    for t, ex in examples.items():
        print(f"\n[{t}] examples:")
        for e in ex:
            print("   ", clip(e))
    if ties:
        ties.sort(key=lambda x: -x[0])
        print(f"\nTies ({len(ties)} templates; the tag listed first wins):")
        for n, first, top in ties[:8]:
            print(f"  ×{n} {top}: {clip(first, 120)}")
    if untagged:
        untagged.sort(key=lambda x: -x[0])
        print(f"\nTop lines that fell into '{default}' (candidates for new rules):")
        for n, first in untagged[: a.top]:
            toks = extract_bracket_tokens(first)
            print(f"  ×{n} {clip(first, 140)}" + (f"   tokens={toks}" if toks else ""))
    return 1 if errors else 0


def main():
    ap = argparse.ArgumentParser(description="Draft and check LogAn tag configs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("tokens"); p.add_argument("logs", nargs="+")
    p.add_argument("--top", type=int, default=40); p.add_argument("--max-lines", type=int, default=300_000)
    p = sub.add_parser("validate"); p.add_argument("config")
    p = sub.add_parser("test"); p.add_argument("config"); p.add_argument("logs", nargs="+")
    p.add_argument("--top", type=int, default=15); p.add_argument("--max-lines", type=int, default=300_000)
    a = ap.parse_args()
    sys.exit({"tokens": cmd_tokens, "validate": cmd_validate, "test": cmd_test}[a.cmd](a))


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:  # output piped into head/less and closed early
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)
