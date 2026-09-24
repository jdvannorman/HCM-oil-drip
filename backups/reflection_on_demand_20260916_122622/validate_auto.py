import sys
from pathlib import Path
PROJECT=Path(r'C:\Users\sam_lee\OneDrive\Codex\HCM Oil Drip Detection')
sys.path.insert(0,str(PROJECT/'.deps'))
import cv2
import numpy as np
import json
import copy
import time
import unittest
from auto_roi import locate_strip,AreaTracker,regions_from_edges,region_quad
from drip_core import DEFAULT_CONFIG,analyze,open_video,ROOT

OUT=ROOT/'validation'
OUT.mkdir(exist_ok=True)
(ROOT/'inspection').mkdir(exist_ok=True)

def synthetic(left,right,shift=0):
    image=np.full((360,640,3),(40,75,110),np.uint8)
    for y in range(0,360,8):cv2.line(image,(0,y),(639,y),(60,95,130),2)
    quad=np.array([[(left+.1+shift)*640,0],[(right-.15+shift)*640,0],[(right+shift)*640,359],[(left+shift)*640,359]],np.int32)
    cv2.fillPoly(image,[quad],(195,160,140))
    return image

def check_ui(folder,report):
    import tkinter as tk
    import drip_app
    drip_app.ROOT=PROJECT
    root=tk.Tk();root.withdraw()
    app=drip_app.App(root)
    try:
        assert '重新掃描' in str(app.header_scan['text'])
        defaults={r['name']:r for r in app.config['regions'] if r['name'] in ('Left','Center','Right')}
        assert min(p[0] for p in defaults['Left']['strip_polygon'])==0
        assert max(p[0] for p in defaults['Left']['strip_polygon'])==min(p[0] for p in defaults['Center']['strip_polygon'])
        assert max(p[0] for p in defaults['Center']['strip_polygon'])==min(p[0] for p in defaults['Right']['strip_polygon'])
        assert max(p[0] for p in defaults['Right']['strip_polygon'])==1
        app.load_video(report['source'],False)
        app.auto_area.set(True);app.change_area_mode()
        assert app.preview_regions
        cfg=app.settings()
        assert cfg['cpu_threads']==app.cpu_choices[app.cpu_mode.get()]
        assert cfg['roi_stable_recheck_seconds']==app.recheck_choices[app.recheck.get()]
        app.set_report(folder,report)
        assert len(app.area_timeline)==report['metadata']['frames']
        if report['events']:
            e=report['events'][0]
            app.tree.selection_set(e['id']);app.choose_event()
            assert app.index==e['peak_frame']
            assert app.current_region()==e['peak_region']
        app.begin_area()
        assert not app.auto_area.get() and not app.reviewing
        assert str(app.stabilize_check['state'])=='normal'
        app.auto_area.set(True);app.change_area_mode()
        assert str(app.stabilize_check['state'])=='disabled'
    finally:app.close()

def main():
    records=[]
    for left,right,shift in [(.18,.82,0),(.29,.71,0),(.35,.65,0),(.24,.72,.04)]:
        found=locate_strip(synthetic(left,right,shift))
        assert found,(left,right,shift)
        for key,expected in [('left',left+shift),('right',right+shift)]:
            measured=np.polyval(found[key],.98)
            assert abs(measured-expected)<.025,(key,measured,expected)
        records.append(dict(test='synthetic_width',left=left,right=right,shift=shift,passed=True))
    blank=np.full((360,640,3),120,np.uint8)
    assert locate_strip(blank) is None
    tracker=AreaTracker(30)
    assert tracker.update(synthetic(.29,.71),0)['status']=='tracking'
    for i in range(1,5):assert tracker.update(blank,i)['status']=='tracking'
    assert tracker.update(blank,5)['status']=='held'
    assert tracker.update(blank,21)['status']=='lost'
    records.append(dict(test='blank_and_hold_timeout',passed=True))
    print('PASS: synthetic widths, lateral offset, blank view, hold timeout',flush=True)
    source=Path(r'C:\Users\sam_lee\OneDrive\WK\Problem Solving\HM Coolant Stain\SIDE TRIMMER')
    names=['2026_08_31__06_34_13.mp4','2026_09_04__10_26_42.mp4','2026_09_15__09_47_49.mp4',
           '2026_09_15__13_58_35_C.mp4','2026_09_15__13_56_27_C.mp4','2026_09_15__13_56_44_C.mp4','2026_09_15__13_58_09_C.mp4']
    for name in names:
        cap,meta=open_video(source/name)
        tracker=AreaTracker(meta['fps'])
        counts={}
        widths=[]
        began=time.perf_counter()
        for i in range(meta['frames']):
            ok,frame=cap.read()
            assert ok
            state=tracker.update(frame,i)
            counts[state['status']]=counts.get(state['status'],0)+1
            if state['edges']:
                regions=regions_from_edges(state['edges'],DEFAULT_CONFIG['regions'])
                widths.append(float(np.polyval(state['edges']['right'],.86)-np.polyval(state['edges']['left'],.86)))
                if i==round(meta['frames']*.4):
                    for r in regions:cv2.polylines(frame,[region_quad(r,meta['width'],meta['height']).astype(np.int32)],True,(0,240,100),4)
                    cv2.imencode('.jpg',cv2.resize(frame,(1280,720)))[1].tofile(str(OUT/(Path(name).stem+'_tracking.jpg')))
        cap.release()
        record=dict(test='actual_video_tracking',name=name,frames=meta['frames'],states=counts,
                    full_edge_checks=tracker.full_checks,camera_checks=tracker.camera_checks,
                    width_fraction_min=min(widths) if widths else None,width_fraction_max=max(widths) if widths else None,
                    elapsed_seconds=round(time.perf_counter()-began,2))
        records.append(record)
        print(json.dumps(record),flush=True)
    cfg=copy.deepcopy(DEFAULT_CONFIG)
    cfg['auto_area']=True
    folder,report=analyze(source/'2026_09_04__10_26_56.mp4',OUT/'analysis',cfg)
    clips=0
    for e in report['events']:
        assert 'peak_region' in e and 'peak_source_bbox' in e
        for key in ['clip','closeup_clip']:
            cap,meta=open_video(folder/e[key])
            count=0
            while cap.read()[0]:count+=1
            cap.release()
            assert count==e['clip_end_frame_exclusive']-e['clip_start_frame']
            clips+=1
    print('PASS: auto analysis, saved geometry, and',clips,'exported clips',flush=True)
    records.append(dict(test='analysis_exports',passed=True,clips=clips,result=str(folder)))
    (OUT/'auto_validation.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    check_ui(folder,report)
    records.append(dict(test='ui_handlers',passed=True))
    (OUT/'auto_validation.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    print('PASS: auto/manual mode switching, saved-geometry playback, event seeking',flush=True)

if __name__=='__main__':
    if '--ui-only' in sys.argv:
        folder=max((OUT/'analysis').glob('*'),key=lambda p:p.stat().st_mtime)
        check_ui(folder,json.loads((folder/'results.json').read_text()))
        print('PASS: auto/manual controls and recorded-area review')
    else:main()
