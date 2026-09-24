import json
from pathlib import Path
from drip_core import analyze, ROOT
from drip_app import SOURCE_DIR,SAMPLES

if __name__=='__main__':
    results=[]
    for name in SAMPLES:
        folder,report=analyze(SOURCE_DIR/name)
        entry=dict(name=name,folder=folder.relative_to(ROOT).as_posix(),candidates=len(report['events']),performance=report['performance'],
                   candidate_drip_events=sum(e['kind']=='candidate_drip_event' for e in report['events']),
                   continuous_activity=sum(e['kind']=='continuous_activity' for e in report['events']),
                   events=[(e['id'],e['region'],e['peak_seconds']) for e in report['events']])
        print(json.dumps(entry),flush=True)
        results.append(entry)
    (ROOT/'output'/'sample_index.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
