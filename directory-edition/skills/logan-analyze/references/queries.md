# Queries for a LogAn output directory

Run each query with the Python inside the LogAn image (pandas and pyarrow are installed), without network and
with the output mounted read-only. Put the query from a section below in place of `<query>`:

```bash
docker run --rm --network none -v <out>:/data/output:ro \
  --entrypoint /opt/app-root/src/.venv/bin/python ghcr.io/log-analyzer/logan:latest -c "<query>"
```

Add `--user <uid>:<gid> --group-add 0` if the output directory is private to your user (mode 700). Times are
epoch seconds and are printed in UTC, matching a run made with `TZ=UTC`. Template `42`, the time and the window
below are examples: change them. Paths in `file_source` are container paths (`/data/in/N/...`).

## Coverage: input lines vs lines kept

```python
import json,pandas as pd; m=json.load(open('/data/output/metrics/preprocessing.json')); e=pd.read_parquet('/data/output/store/log_entries.parquet',columns=['original_text']); kept=len(e)+int(e.original_text.str.count(chr(10)).sum()); t=m['num_log_lines_total']; print('input lines',t,'kept',kept,'dropped',t-kept,str(round(100*(t-kept)/t,1))+'%')
```

Files skipped entirely are listed in `developer_debug_files/ignored_files.log`; their lines are not part of
`num_log_lines_total`.

## Problem templates, earliest first, with first and last occurrence

```python
import pandas as pd; t=pd.read_parquet('/data/output/store/templates.parquet'); e=pd.read_parquet('/data/output/store/log_entries.parquet',columns=['template_id','timestamp']); s=e[e.timestamp>0].groupby('template_id').timestamp.agg(['min','max']); t=t.join(s,on='template_id'); t['first']=pd.to_datetime(t['min'],unit='s'); t['last']=pd.to_datetime(t['max'],unit='s'); p=t[~t.golden_signal.str.lower().isin(['information','info'])]; print(p.sort_values('first')[['template_id','golden_signal','fault_category','occurrence_count','first','last','rep_log']].head(15).to_string(index=False,max_colwidth=120))
```

For rare templates sort by `occurrence_count` ascending; for the highest volume, descending.

## Peak minutes of problem entries

```python
import pandas as pd; t=pd.read_parquet('/data/output/store/templates.parquet'); e=pd.read_parquet('/data/output/store/log_entries.parquet',columns=['template_id','timestamp']); bad=set(t[~t.golden_signal.str.lower().isin(['information','info'])].template_id); x=e[e.template_id.isin(bad)&(e.timestamp>0)]; v=(x.timestamp//60*60).value_counts().head(8); v.index=pd.to_datetime(v.index,unit='s'); print(v.to_string())
```

## Entries of one template over time

```python
import pandas as pd; e=pd.read_parquet('/data/output/store/log_entries.parquet'); x=e[e.template_id==42].sort_values(['timestamp','entry_id']).copy(); x['time']=pd.to_datetime(x.timestamp,unit='s'); print(len(x)); print(x[['time','file_source','original_text']].head(20).to_string(index=False,max_colwidth=200))
```

## Variable values of one template

```python
import json,collections,pandas as pd; e=pd.read_parquet('/data/output/store/log_entries.parquet',columns=['template_id','variables']); c=collections.defaultdict(collections.Counter); [c[i].update([v]) for vs in e[e.template_id==42].variables for i,v in enumerate(json.loads(vs))]; [print('var'+str(i),len(c[i]),c[i].most_common(5)) for i in sorted(c)]
```

## Problem entries in a window around a moment

Window of ±10 minutes around 14:05 (UTC); add `information` templates by removing the `isin(bad)` filter.

```python
import pandas as pd; t=pd.read_parquet('/data/output/store/templates.parquet'); e=pd.read_parquet('/data/output/store/log_entries.parquet'); c=pd.Timestamp('2026-11-07 14:05').timestamp(); w=600; bad=set(t[~t.golden_signal.str.lower().isin(['information','info'])].template_id); x=e[(e.timestamp>=c-w)&(e.timestamp<=c+w)&e.template_id.isin(bad)].sort_values('timestamp').copy(); x['time']=pd.to_datetime(x.timestamp,unit='s'); print(x[['time','template_id','file_source','original_text']].head(40).to_string(index=False,max_colwidth=200))
```

## Search templates by text

```python
import pandas as pd; t=pd.read_parquet('/data/output/store/templates.parquet'); m=t[t.template.str.contains('timeout|refused|OOM',case=False)|t.rep_log.str.contains('timeout|refused|OOM',case=False)]; print(m.sort_values('occurrence_count',ascending=False)[['template_id','golden_signal','fault_category','occurrence_count','rep_log']].to_string(index=False,max_colwidth=140))
```

## Custom tags (runs with `--tag-config`)

```python
import pandas as pd; t=pd.read_parquet('/data/output/store/templates.parquet'); print(t.groupby('component',dropna=False).occurrence_count.agg(['count','sum']).sort_values('sum',ascending=False).to_string())
```

Keep the output of each query small (`head`, `max_colwidth`) and quote only the lines you need.
