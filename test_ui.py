"""Exercise application handlers without screen capture or native input injection."""
import copy
import json
from pathlib import Path
import tempfile
import tkinter as tk
from drip_app import App,ROOT
from drip_core import save_json

root=tk.Tk()
root.withdraw()
app=App(root)
root.update_idletasks()
assert app.current is not None
assert app.meta['width']==1920
app.step(1)
assert app.index==1
app.step(-1)
assert app.index==0
paths=list((ROOT/'output').glob('*/results.json'))
report_path=next(p for p in paths if '10_26_56_' in p.parent.name)
report=json.loads(report_path.read_text(encoding='utf-8'))
with tempfile.TemporaryDirectory(dir=ROOT/'inspection') as tmp:
    folder=Path(tmp)
    app.load_video(report['source'],False)
    app.set_report(folder,copy.deepcopy(report))
    assert len(app.tree.get_children())==len(report['events'])
    event=report['events'][0]
    app.tree.selection_set(event['id'])
    app.choose_event()
    assert app.index==event['peak_frame']
    assert app.zoom.get()
    app.drip_count.set('1')
    app.note.set('Automated UI handler test')
    app.mark('accepted')
    saved=json.loads((folder/'results.json').read_text(encoding='utf-8'))
    assert saved['events'][0]['status']=='accepted'
    assert saved['events'][0]['confirmed_drips']==1
    app.mark('rejected')
    saved=json.loads((folder/'results.json').read_text(encoding='utf-8'))
    assert saved['events'][0]['status']=='rejected'
    assert saved['events'][0]['confirmed_drips'] is None
    assert (folder/'events.csv').exists()
app.close()
print('PASS: video load, frame stepping, result load, event seek, zoom, accept/reject, JSON/CSV persistence')
