import sys
from pathlib import Path
PROJECT=Path(r'C:\Users\sam_lee\OneDrive\Codex\HCM Oil Drip Detection')
sys.path.insert(0,str(PROJECT/'.deps'))
import cv2
import numpy as np
import copy
import json
from validate_auto import synthetic
from drip_core import ROOT,DEFAULT_CONFIG,analyze

out=ROOT/'validation'
source=out/'synthetic_width_change.mp4'
writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),30,(640,360))
assert writer.isOpened()
for i in range(150):
    left,right=(.22,.78) if i<60 else (.32,.68)
    frame=synthetic(left,right)
    if i in [20,21,22,110,111,112]:
        y=300+(i%10)*5
        edge=left+.1*(1-y/360)
        cv2.circle(frame,(int((edge+.065)*640),y),4,(255,255,255),-1)
    writer.write(frame)
writer.release()
cfg=copy.deepcopy(DEFAULT_CONFIG)
cfg.update(auto_area=True,export_clips=False)
folder,report=analyze(source,out/'width_change_results',cfg)
events=report['events']
assert len(events)==2,[(e['peak_frame'],e['region']) for e in events]
assert all(e['region']=='Left' for e in events)
assert 20<=events[0]['peak_frame']<=22 and 110<=events[1]['peak_frame']<=112
timeline=json.loads((folder/'region_timeline.json').read_text())
assert any(r['status']=='recalibrating' for r in timeline[60:80])
assert timeline[90]['status']=='tracking'
old=timeline[40]['regions'][0]['box'][0]
new=timeline[90]['regions'][0]['box'][0]
assert new-old>.07,(old,new)
assert all(not 58<=e['peak_frame']<=82 for e in events)
print('PASS: in-video width change, background reset, no false event on change, two true synthetic events recovered')
