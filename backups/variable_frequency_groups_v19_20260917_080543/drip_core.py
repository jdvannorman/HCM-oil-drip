"""CPU-only offline coolant-event detector. Counts candidates, not certified droplets."""
from __future__ import annotations
import argparse
import csv
import json
import math
import os
import platform
import sys
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Event

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / '.deps'))
import cv2
import numpy as np

LOGICAL_CPUS=os.cpu_count() or 8
CPU_IDENTIFIER=os.environ.get('PROCESSOR_IDENTIFIER') or platform.processor() or platform.machine()
DEFAULT_CPU_THREADS=max(2,min(12,LOGICAL_CPUS-2 if LOGICAL_CPUS>4 else LOGICAL_CPUS))

DEFAULT_CONFIG = {
    'version': 4,
    'regions': [
        {'name': 'Left', 'box': [0.319, 0.759, 0.440, 0.988],
         'polygon': [[0,0],[1,0],[1,1],[0,1]],
         'strip_polygon': [[0,.72],[1/3,.72],[1/3,.98],[0,.98]]},
        {'name': 'Center', 'box': [0.440, 0.720, 0.619, 0.980],
         'polygon': [[0,0],[1,0],[1,1],[0,1]],
         'strip_polygon': [[1/3,.72],[2/3,.72],[2/3,.98],[1/3,.98]]},
        {'name': 'Right', 'box': [0.619, 0.719, 0.739, 0.954],
         'polygon': [[0,0],[1,0],[1,1],[0,1]],
         'strip_polygon': [[2/3,.72],[1,.72],[1,.98],[2/3,.98]]},
    ],
    'threshold': 14.0,
    'min_area': 8,
    'max_area': 1800,
    'merge_gap_seconds': 0.13,
    'continuous_seconds': 0.75,
    'pre_seconds': 0.75,
    'post_seconds': 0.75,
    'stabilize': True,
    'export_clips': False,
    'auto_area': False,
    'cpu_threads': 'auto',
    'cpu_mode': 'auto',
    'roi_startup_seconds': 5.0,
    'roi_startup_stride': 5,
    'roi_camera_check_seconds': 1.0,
    'roi_stable_recheck_seconds': 600.0,
}

class Cancelled(Exception):
    pass

def peak_memory_mb():
    if sys.platform!='win32':return None
    try:
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD),
                      ('PeakWorkingSetSize',ctypes.c_size_t),('WorkingSetSize',ctypes.c_size_t),
                      ('QuotaPeakPagedPoolUsage',ctypes.c_size_t),('QuotaPagedPoolUsage',ctypes.c_size_t),
                      ('QuotaPeakNonPagedPoolUsage',ctypes.c_size_t),('QuotaNonPagedPoolUsage',ctypes.c_size_t),
                      ('PagefileUsage',ctypes.c_size_t),('PeakPagefileUsage',ctypes.c_size_t)]
        kernel=ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype=wintypes.HANDLE
        info=Counters()
        info.cb=ctypes.sizeof(info)
        fn=ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
        if fn(kernel.GetCurrentProcess(),ctypes.byref(info),info.cb):return round(info.PeakWorkingSetSize/1024**2,1)
    except (AttributeError,OSError):pass
    return None

def check_cancel(cancel,pause_gate=None):
    if cancel and cancel.is_set():
        raise Cancelled('Analysis cancelled. Incomplete output remains marked as cancelled.')
    if pause_gate:
        while not pause_gate.wait(.1):
            if cancel and cancel.is_set():
                raise Cancelled('Analysis cancelled. Incomplete output remains marked as cancelled.')

def validate_config(config):
    if not config.get('regions'):
        raise ValueError('Select at least one detection area.')
    names = []
    for region in config['regions']:
        names.append(region['name'])
        x1, y1, x2, y2 = region['box']
        if not all(math.isfinite(v) for v in region['box']) or not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError('Detection areas must be inside the image.')
        poly=region.get('polygon',[[0,0],[1,0],[1,1],[0,1]])
        if len(poly)<3 or not all(len(p)==2 and all(math.isfinite(v) and 0<=v<=1 for v in p) for p in poly):
            raise ValueError('Area polygon must contain at least three normalized points.')
        strip_poly=region.get('strip_polygon')
        if strip_poly is not None and (len(strip_poly)<3 or not all(
                len(p)==2 and all(math.isfinite(v) and 0<=v<=1 for v in p) for p in strip_poly)):
            raise ValueError('Strip-relative polygon must contain at least three points inside the strip.')
    if len(set(names)) != len(names):
        raise ValueError('Area names must be unique.')
    for key in ['threshold', 'min_area', 'max_area', 'merge_gap_seconds', 'pre_seconds', 'post_seconds']:
        v = config[key]
        if not math.isfinite(v) or v < 0:
            raise ValueError(f'{key} must be a finite, non-negative number.')
    if config['threshold'] < 1 or config['min_area'] < 1 or config['max_area'] < config['min_area']:
        raise ValueError('Invalid contrast threshold or component area.')
    threads=config.get('cpu_threads','auto')
    if threads!='auto' and (not isinstance(threads,(int,float)) or not math.isfinite(threads) or int(threads)!=threads or not 1<=threads<=LOGICAL_CPUS):
        raise ValueError(f'CPU threads must be a whole number from 1 to {LOGICAL_CPUS}.')
    for key,minimum in [('roi_startup_seconds',0),('roi_camera_check_seconds',.1),('roi_stable_recheck_seconds',1)]:
        value=config.get(key,DEFAULT_CONFIG[key])
        if not isinstance(value,(int,float)) or not math.isfinite(value) or value<minimum:
            raise ValueError(f'{key} has an invalid interval.')
    stride=config.get('roi_startup_stride',5)
    if not isinstance(stride,(int,float)) or int(stride)!=stride or not 1<=stride<=120:
        raise ValueError('Startup area-check stride must be from 1 to 120 frames.')

def configure_cpu(config):
    requested=config.get('cpu_threads','auto')
    threads=int(config.get('resolved_cpu_threads') or auto_select_cpu_threads()) if requested=='auto' else int(requested)
    cv2.setNumThreads(threads)
    config['resolved_cpu_threads']=threads
    config['cpu_mode']='auto' if requested=='auto' else 'manual'
    return threads

def open_video(path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f'Cannot decode video: {path}')
    fps = cap.get(cv2.CAP_PROP_FPS)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w, h = int(cap.get(3)), int(cap.get(4))
    if not math.isfinite(fps) or fps <= 0 or n <= 0 or min(w,h) < 32:
        cap.release()
        raise ValueError('Video has invalid frame rate, dimensions, or frame count.')
    return cap, dict(width=w, height=h, fps=fps, frames=n, duration_seconds=n/fps)

def pixel_box(region, w, h):
    x1,y1,x2,y2 = region['box']
    box = (int(x1*w), int(y1*h), min(w,int(x2*w)), min(h,int(y2*h)))
    if box[2]-box[0] < 12 or box[3]-box[1] < 12:
        raise ValueError('Detection area is too small (minimum 12 x 12 pixels).')
    return box

class Stabilizer:
    """Estimate camera movement using features outside the central moving strip."""
    def __init__(self, frame, enabled=True):
        self.enabled = enabled
        self.scale = 640 / frame.shape[1]
        self.ref = cv2.cvtColor(cv2.resize(frame, (640, round(frame.shape[0]*self.scale))), cv2.COLOR_BGR2GRAY)
        h,w=self.ref.shape
        mask=np.zeros_like(self.ref)
        mask[:, :int(w*.22)] = 255
        mask[:, int(w*.83):] = 255
        mask[:int(h*.17), :] = 255
        self.points = cv2.goodFeaturesToTrack(self.ref, 180, .03, 8, mask=mask)

    def transform(self, frame):
        identity=np.array([[1,0,0],[0,1,0]], dtype=np.float32)
        if not self.enabled:
            return identity, True
        if self.points is None or len(self.points)<8:
            return identity, False
        gray=cv2.cvtColor(cv2.resize(frame,(self.ref.shape[1],self.ref.shape[0])),cv2.COLOR_BGR2GRAY)
        pts, status, errors=cv2.calcOpticalFlowPyrLK(self.ref, gray, self.points, None, winSize=(21,21), maxLevel=3)
        if pts is None:
            return identity, False
        good=(status.ravel()==1)&(errors.ravel()<35)
        if good.sum()<8:
            return identity, False
        matrix,inliers=cv2.estimateAffinePartial2D(self.points[good],pts[good],method=cv2.RANSAC,ransacReprojThreshold=1.5)
        if matrix is None or inliers.sum()<8:
            return identity, False
        scale=math.hypot(matrix[0,0],matrix[1,0])
        if not (.97<scale<1.03) or np.linalg.norm(matrix[:,2])>30:
            return identity, False
        matrix[:,2]/=self.scale
        return matrix.astype(np.float32), True

def aligned_crop(frame, box, transform):
    x1,y1,x2,y2=box
    matrix=transform.copy()
    matrix[:,2]+=matrix[:,:2] @ np.array([x1,y1],dtype=np.float32)
    crop=cv2.warpAffine(frame,matrix,(x2-x1,y2-y1),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP,borderMode=cv2.BORDER_REFLECT)
    return crop

def gray_blur(crop):
    return cv2.GaussianBlur(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),(3,3),0).astype(np.float32)

class RegionDetector:
    def __init__(self, name, box, background, config):
        self.name, self.box, self.background, self.config = name, box, background, config
        region=next(r for r in config['regions'] if r['name']==name)
        h,w=background.shape
        self.valid=np.zeros((h,w),np.uint8)
        points=np.array(region.get('polygon',[[0,0],[1,0],[1,1],[0,1]]))*[w-1,h-1]
        cv2.fillPoly(self.valid,[points.astype(np.int32)],255)
        # Exclude textured machinery and fixed reflection edges in the initial
        # background. These can shimmer on every frame without a coolant event.
        texture=np.abs(background-cv2.GaussianBlur(background,(0,0),3))>5
        texture=cv2.dilate(texture.astype(np.uint8),np.ones((9,9),np.uint8))
        self.valid[texture>0]=0
        self.texture_valid=np.where(texture>0,0,255).astype(np.uint8)
        self.dynamic_polygon_key=None
        self.dynamic_valid=None
        self.open_kernel=np.ones((2,2),np.uint8)

    def detect(self, crop, polygon=None):
        gray=gray_blur(crop)
        diff=gray-self.background
        # Remove broad illumination/reflection changes, preserving small spots.
        local=diff-cv2.GaussianBlur(diff,(0,0),5)
        threshold=self.config['threshold']
        mask=((local>threshold)&(diff>threshold)).astype(np.uint8)*255
        valid=self.valid
        if polygon is not None:
            key=tuple(tuple(float(value) for value in point) for point in polygon)
            if key!=self.dynamic_polygon_key:
                dynamic=np.zeros_like(self.valid)
                points=np.array(polygon,dtype=np.float32)*[dynamic.shape[1]-1,dynamic.shape[0]-1]
                cv2.fillPoly(dynamic,[points.astype(np.int32)],255)
                self.dynamic_valid=cv2.bitwise_and(dynamic,self.texture_valid)
                self.dynamic_polygon_key=key
            valid=self.dynamic_valid
        mask=cv2.bitwise_and(mask,valid)
        mask[:4,:]=mask[-4:,:]=mask[:,:4]=mask[:,-4:]=0
        mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,self.open_kernel)
        count,labels,stats,centers=cv2.connectedComponentsWithStats(mask)
        spots=[]
        for i in range(1,count):
            x,y,w,h,area=[int(v) for v in stats[i]]
            if not self.config['min_area'] <= area <= self.config['max_area']:
                continue
            if w>90 or h>120 or max(w/h,h/w)>8:
                continue
            contrast=float(local[labels==i].max())
            spots.append(dict(box=[x,y,x+w,y+h],area=area,contrast=round(contrast,2),center=centers[i].tolist()))
        # Adapt even if a reflection persists; short transients remain detectable.
        cv2.accumulateWeighted(gray,self.background,.08)
        return spots

def auto_select_cpu_threads(force=False):
    """Micro-benchmark the real per-region detector primitives and cache the winner."""
    candidates=sorted(set([max(2,round(LOGICAL_CPUS*.35)),DEFAULT_CPU_THREADS,max(2,LOGICAL_CPUS-2)]))
    profile_path=ROOT/'cpu_tuning_generic.json'
    signature={'cpu_identifier':CPU_IDENTIFIER,'logical_cpus':LOGICAL_CPUS,'opencv':cv2.__version__,
               'algorithm':'independent_regions_v1','candidates':candidates}
    if not force and profile_path.exists():
        try:
            saved=json.loads(profile_path.read_text(encoding='utf-8'))
            if all(saved.get(key)==value for key,value in signature.items()) and saved.get('selected') in candidates:
                return int(saved['selected'])
        except (OSError,ValueError,TypeError):pass
    rng=np.random.default_rng(14500)
    sources=[rng.integers(0,256,(286,430,3),dtype=np.uint8) for _ in range(3)]
    backgrounds=[rng.normal(112,18,(224,224)).astype(np.float32) for _ in range(3)]
    kernel=np.ones((2,2),np.uint8)
    scores={}
    for threads in candidates:
        cv2.setNumThreads(threads)
        trials=[]
        for trial in range(4):
            began=time.perf_counter()
            for repeat in range(18):
                crops=[cv2.resize(source,(224,224),interpolation=cv2.INTER_LINEAR) for source in sources]
                grays=[gray_blur(crop) for crop in crops]
                differences=[gray-background for gray,background in zip(grays,backgrounds)]
                smooth=[cv2.GaussianBlur(diff,(0,0),5) for diff in differences]
                for diff,blurred in zip(differences,smooth):
                    mask=((diff-blurred>14)&(diff>14)).astype(np.uint8)*255
                    mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,kernel)
                    cv2.connectedComponentsWithStats(mask)
            if trial:trials.append(time.perf_counter()-began)
        scores[str(threads)]=round(float(np.median(trials)),6)
    selected=min(candidates,key=lambda value:scores[str(value)])
    save_json(profile_path,{**signature,'selected':selected,'seconds':scores,'created_at':time.strftime('%Y-%m-%dT%H:%M:%S')})
    return selected

def group_events(observations, fps, gap):
    """One connected temporal burst per area = one candidate splash/event.

    Simultaneous droplets within an area can merge. This is explicitly not a
    physical droplet counter. Frame counts never serve as drip counts.
    """
    result=[]
    active=None
    max_gap=max(1,round(gap*fps))
    for obs in observations:
        if active is None or obs['frame']-active['last_frame']>max_gap:
            active=dict(region=obs['region'],start_frame=obs['frame'],last_frame=obs['frame'],
                        peak_frame=obs['frame'],peak_score=obs['score'],bbox=obs['bbox'],positive_frames=1,
                        max_components=obs['components'],status='unreviewed',confirmed_drips=None,notes='',
                        peak_transform=obs.get('transform',[[1,0,0],[0,1,0]]))
            result.append(active)
            for key in ('peak_source_bbox','peak_region'):
                if key in obs:active[key]=obs[key]
        else:
            active['last_frame']=obs['frame']
            active['positive_frames']+=1
            active['max_components']=max(active['max_components'],obs['components'])
            if obs['score']>active['peak_score']:
                active.update(peak_frame=obs['frame'],peak_score=obs['score'],bbox=obs['bbox'],
                              peak_transform=obs.get('transform',[[1,0,0],[0,1,0]]))
                for key in ('peak_source_bbox','peak_region'):
                    if key in obs:active[key]=obs[key]
    for event in result:
        event.update(start_seconds=round(event['start_frame']/fps,4),
                     end_seconds=round((event['last_frame']+1)/fps,4),
                     peak_seconds=round(event['peak_frame']/fps,4))
    return result

def classify_regular_patterns(events, metadata, window_seconds=60.0, min_occurrences=4,
                              minimum_regularity=.65):
    """Mark spatially stable, evenly repeated candidates without deleting events.

    This is a review aid, not an automatic reflection rejection rule. A truly
    periodic leak can receive the same label and must remain available for review.
    """
    fps=float(metadata.get('fps') or 30)
    width,height=float(metadata.get('width') or 1),float(metadata.get('height') or 1)
    radius=max(18.0,min(52.0,math.hypot(width,height)*.018))
    for event in events:
        event['pattern_id']=''
        event['pattern_class']='unclassified'
        event.pop('pattern_interval_seconds',None)
    located=[]
    for event in events:
        box=event.get('peak_source_bbox')
        if box:
            center=((float(box[0])+float(box[2]))/2,(float(box[1])+float(box[3]))/2)
        elif event.get('bbox'):
            bx0,by0,bx1,by1=event['bbox']
            point=np.array([[(bx0+bx1)/2,(by0+by1)/2,1]],np.float32)
            transform=np.array(event.get('peak_transform',[[1,0,0],[0,1,0]]),np.float32)
            raw=point@transform.T
            center=(float(raw[0,0]),float(raw[0,1]))
        else:continue
        located.append((event,center))
    clusters=[]
    for event,center in sorted(located,key=lambda item:item[0]['peak_seconds']):
        choices=[cluster for cluster in clusters if cluster['region']==event['region'] and
                 math.hypot(center[0]-cluster['center'][0],center[1]-cluster['center'][1])<=radius]
        if choices:
            cluster=min(choices,key=lambda item:math.hypot(center[0]-item['center'][0],center[1]-item['center'][1]))
            cluster['items'].append((event,center))
            n=len(cluster['items'])
            cluster['center']=((cluster['center'][0]*(n-1)+center[0])/n,(cluster['center'][1]*(n-1)+center[1])/n)
        else:clusters.append({'region':event['region'],'center':center,'items':[(event,center)]})
    candidates=[]
    for cluster_index,cluster in enumerate(clusters):
        items=sorted(cluster['items'],key=lambda item:item[0]['peak_seconds'])
        times=np.array([item[0]['peak_seconds'] for item in items],np.float64)
        for start in range(len(items)-min_occurrences+1):
            maximum=min(len(items),start+20)
            for end in range(start+min_occurrences,maximum+1):
                subset=times[start:end]
                if subset[-1]-subset[0]>window_seconds:break
                intervals=np.diff(subset)
                period=float(np.median(intervals))
                if period<max(.10,3/fps) or period>window_seconds/(min_occurrences-1):continue
                tolerance=max(2/fps,.16*period)
                deviations=np.abs(intervals-period)
                if float(deviations.max(initial=0))>tolerance:continue
                score=max(0.0,1.0-float(np.mean(deviations))/tolerance)
                candidates.append({'cluster':cluster_index,'indices':set(range(start,end)),'period':period,'score':score,'items':items})
    merged=[]
    for candidate in sorted(candidates,key=lambda item:(item['cluster'],min(item['indices']),-len(item['indices']))):
        target=next((group for group in merged if group['cluster']==candidate['cluster'] and
                     group['indices']&candidate['indices'] and
                     abs(group['period']-candidate['period'])<=max(2/fps,.20*group['period'])),None)
        if target:
            target['indices']|=candidate['indices']
            target['period']=float(np.median([target['period'],candidate['period']]))
            target['score']=max(target['score'],candidate['score'])
        else:merged.append(dict(candidate))
    patterns=[]
    for group in merged:
        pattern_events=[group['items'][i][0] for i in sorted(group['indices'])]
        if len(pattern_events)<min_occurrences or any(event.get('pattern_id') for event in pattern_events):continue
        times=[event['peak_seconds'] for event in pattern_events]
        intervals=np.diff(np.array(times,np.float64))
        period_value=float(np.median(intervals))
        tolerance=max(2/fps,.16*period_value)
        deviations=np.abs(intervals-period_value)
        regularity=max(0.0,1.0-float(np.mean(deviations))/tolerance)
        # Recheck after overlapping candidate windows were merged. This avoids
        # promoting a chain whose small subwindows look regular but whose full
        # sequence has drifted or contains an accidental interval.
        if float(deviations.max(initial=0))>tolerance or regularity<minimum_regularity:continue
        pattern_id=f'R{len(patterns)+1:03d}'
        centers=[group['items'][i][1] for i in sorted(group['indices'])]
        representative=max(pattern_events,key=lambda event:event.get('peak_score',0))
        period=round(period_value,3)
        pattern=dict(id=pattern_id,classification='regular_location_frequency',region=pattern_events[0]['region'],
                     occurrences=len(pattern_events),event_ids=[event['id'] for event in pattern_events],
                     representative_event_id=representative['id'],first_seconds=round(min(times),3),last_seconds=round(max(times),3),
                     interval_seconds=period,frequency_per_minute=round(60/period,2),regularity_score=round(regularity,3),
                     center_source_pixels=[round(float(np.mean([p[0] for p in centers])),1),round(float(np.mean([p[1] for p in centers])),1)],
                     review_hint='Regular location and timing; possible machine-synchronous reflection or periodic process defect. Human review required.')
        patterns.append(pattern)
        for event in pattern_events:
            event.update(pattern_id=pattern_id,pattern_class='regular',pattern_interval_seconds=period)
    return patterns

def save_json(path, data):
    path=Path(path)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
    temp.replace(path)

def write_csv(folder, events):
    fields=['id','region','kind','start_seconds','peak_seconds','end_seconds',
            'pattern_id','pattern_class','pattern_interval_seconds',
            'status','confirmed_drips','positive_frames','max_components','peak_score','clip','closeup_clip','notes']
    with (Path(folder)/'events.csv').open('w',newline='',encoding='utf-8-sig') as file:
        writer=csv.DictWriter(file,fieldnames=fields,extrasaction='ignore')
        writer.writeheader()
        writer.writerows(events)

def write_patterns_csv(folder, patterns):
    fields=['id','classification','region','occurrences','first_seconds','last_seconds',
            'interval_seconds','frequency_per_minute','regularity_score',
            'representative_event_id','center_source_pixels','event_ids','review_hint']
    rows=[]
    for pattern in patterns:
        row=dict(pattern)
        row['center_source_pixels']=';'.join(str(value) for value in pattern.get('center_source_pixels',[]))
        row['event_ids']=';'.join(pattern.get('event_ids',[]))
        rows.append(row)
    with (Path(folder)/'regular_patterns.csv').open('w',newline='',encoding='utf-8-sig') as file:
        writer=csv.DictWriter(file,fieldnames=fields,extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)

def create_writer(path, fps, size):
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),fps,size)
    if not writer.isOpened():
        raise RuntimeError('The MP4 encoder could not start.')
    return writer

def export_event(cap, metadata, event, box, folder, config, cancel, timeline=None, pause_gate=None):
    fps=metadata['fps']
    start=max(0,event['start_frame']-round(config['pre_seconds']*fps))
    end=min(metadata['frames'],event['last_frame']+1+round(config['post_seconds']*fps))
    event['clip_start_seconds']=round(start/fps,4)
    event['clip_end_seconds']=round(end/fps,4)
    event['clip_peak_seconds']=round((event['peak_frame']-start)/fps,4)
    event['clip_start_frame']=start
    event['clip_end_frame_exclusive']=end
    stem=f"{event['id']}_{event['region'].lower()}_{event['peak_seconds']:.3f}s"
    full_path=folder/(stem+'.mp4')
    crop_path=folder/(stem+'_closeup.mp4')
    image_path=folder/(stem+'.jpg')
    x1,y1,x2,y2=box
    # Include 40 pixels of context; fixed crop in source coordinates for review.
    pad=40
    xa,ya=max(0,x1-pad),max(0,y1-pad)
    xb,yb=min(metadata['width'],x2+pad),min(metadata['height'],y2+pad)
    cw,ch=(xb-xa)//2*2,(yb-ya)//2*2
    full=None
    close=None
    try:
        if config['export_clips']:
            fw,fh=metadata['width']//2*2,metadata['height']//2*2
            full=create_writer(full_path,fps,(fw,fh))
            close=create_writer(crop_path,fps,(cw*2,ch*2))
        cap.set(cv2.CAP_PROP_POS_FRAMES,start)
        for index in range(start,end):
            check_cancel(cancel,pause_gate)
            ok,frame=cap.read()
            if not ok:
                raise RuntimeError(f'Video could not decode frame {index} during export.')
            dynamic_region=None
            area_status='manual'
            if timeline:
                area_status=timeline[index]['status']
                dynamic_region=next((r for r in timeline[index]['regions'] if r['name']==event['region']),event.get('peak_region'))
            if dynamic_region:
                x1,y1,x2,y2=pixel_box(dynamic_region,metadata['width'],metadata['height'])
                xa,ya=max(0,x1-pad),max(0,y1-pad)
                xb,yb=min(metadata['width'],x2+pad),min(metadata['height'],y2+pad)
            else:xb,yb=xa+cw,ya+ch
            crop=frame[ya:yb,xa:xb].copy()
            crop=cv2.resize(crop,(cw*2,ch*2))
            if index==event['peak_frame']:
                if 'peak_source_bbox' in event:
                    bx1,by1,bx2,by2=event['peak_source_bbox']
                    raw=np.array([[bx1,by1],[bx2,by2]],np.float32)
                else:
                    bx1,by1,bx2,by2=event['bbox']
                    corners=np.array([[x1+bx1,y1+by1,1],[x1+bx2,y1+by2,1]],dtype=np.float32)
                    raw=corners @ np.array(event['peak_transform'],dtype=np.float32).T
                raw=(raw-[xa,ya])*[cw*2/(xb-xa),ch*2/(yb-ya)]
                a,b=raw.astype(int)
                cv2.rectangle(crop,(a[0]-6,a[1]-6),(b[0]+6,b[1]+6),(0,180,255),2)
            active=event['start_frame']<=index<=event['last_frame']
            color=(0,180,255) if active else (160,210,90)
            if dynamic_region:
                from auto_roi import region_quad
                cv2.polylines(frame,[region_quad(dynamic_region,metadata['width'],metadata['height']).astype(np.int32)],True,color,2)
            else:cv2.rectangle(frame,(x1,y1),(x2,y2),color,2)
            event_label='CONTINUOUS ACTIVITY' if event.get('kind')=='continuous_activity' else 'CANDIDATE - REVIEW'
            label=f"{event['id']}  {event['region']}  {index/fps:.3f}s  {event_label}  {area_status}"
            cv2.rectangle(frame,(0,0),(min(metadata['width'],1000),42),(25,30,36),-1)
            cv2.putText(frame,label,(12,29),cv2.FONT_HERSHEY_SIMPLEX,.65,(255,255,255),1,cv2.LINE_AA)
            cv2.rectangle(crop,(0,0),(crop.shape[1],32),(25,30,36),-1)
            cv2.putText(crop,f"{index/fps:.3f}s  {'EVENT' if active else 'context'}",(8,23),cv2.FONT_HERSHEY_SIMPLEX,.6,color,1,cv2.LINE_AA)
            if index==event['peak_frame']:
                cv2.imencode('.jpg',crop)[1].tofile(str(image_path))
            if full:
                full.write(frame[:fh,:fw])
                close.write(crop)
    finally:
        if full: full.release()
        if close: close.release()
    event['snapshot']=str(image_path.relative_to(folder.parent))
    event['clip']=str(full_path.relative_to(folder.parent)) if config['export_clips'] else ''
    event['closeup_clip']=str(crop_path.relative_to(folder.parent)) if config['export_clips'] else ''

def analyze(path, output_root=None, config=None, progress=None, cancel=None, pause_gate=None, partial=None):
    config=json.loads(json.dumps(config or DEFAULT_CONFIG))
    validate_config(config)
    if config.get('auto_area',False):
        from auto_analysis import analyze_auto
        return analyze_auto(path,output_root,config,progress,cancel,pause_gate,partial)
    cpu_threads=configure_cpu(config)
    path=Path(path).resolve()
    cap,metadata=open_video(path)
    folder=Path(output_root or ROOT/'output')/(path.stem+'_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:5])
    folder.mkdir(parents=True,exist_ok=False)
    clips=folder/'clips'
    clips.mkdir()
    report=dict(version=1,state='running',source=str(path),metadata=metadata,config=config,events=[],
                count_definition='Candidate coolant bursts per area. Not individual physical droplets.',
                time_basis='Source presentation timestamp when available; otherwise frame index / average FPS. No plant clock synchronization.',
                warnings=[],performance={})
    save_json(folder/'results.json',report)
    start_time=time.perf_counter()
    def update(value,message):
        if progress: progress(value,message)
    def emit_empty_partial():
        if not partial:return
        snapshot=json.loads(json.dumps(report))
        snapshot.update(state='paused_preview',events=[],
                        scan_progress=dict(scanned_through_frame=-1,frames_scanned=0,total_frames=metadata['frames'],seconds_scanned=0),
                        warnings=['Paused during initial calibration. No detection frames have been scanned yet.'],performance={})
        partial(folder,snapshot)
    try:
        ok,first=cap.read()
        if not ok: raise RuntimeError('Cannot decode first frame.')
        stabilizer=Stabilizer(first,config['stabilize'])
        boxes={r['name']:pixel_box(r,metadata['width'],metadata['height']) for r in config['regions']}
        update(0,'Building a local background from the first second…')
        samples={name:[] for name in boxes}
        cap.set(cv2.CAP_PROP_POS_FRAMES,0)
        for i in range(min(metadata['frames'],max(3,min(31,round(metadata['fps']))))):
            if pause_gate and not pause_gate.is_set():emit_empty_partial()
            check_cancel(cancel,pause_gate)
            ok,frame=cap.read()
            if not ok: raise RuntimeError(f'Cannot decode calibration frame {i}.')
            matrix,stable=stabilizer.transform(frame)
            for name,box in boxes.items():
                samples[name].append(gray_blur(aligned_crop(frame,box,matrix)))
        detectors={name:RegionDetector(name,box,np.median(np.stack(samples[name]),axis=0).astype(np.float32),config) for name,box in boxes.items()}
        del samples
        observations={name:[] for name in boxes}
        unstable=0
        cap.set(cv2.CAP_PROP_POS_FRAMES,0)
        timestamps=[]
        def current_events():
            events=[]
            for name,items in observations.items():events+=group_events(items,metadata['fps'],config['merge_gap_seconds'])
            events.sort(key=lambda e:(e['start_frame'],e['region']))
            use_pts=len(timestamps)>1 and all(b>a for a,b in zip(timestamps,timestamps[1:]))
            for i,event in enumerate(events,1):
                event['id']=f'E{i:03d}'
                event['kind']='continuous_activity' if (event['last_frame']+1-event['start_frame'])/metadata['fps']>=config.get('continuous_seconds',.75) else 'candidate_drip_event'
                if use_pts:
                    event['start_seconds']=round(timestamps[event['start_frame']],4)
                    event['peak_seconds']=round(timestamps[event['peak_frame']],4)
                    last=event['last_frame']+1
                    event['end_seconds']=round(timestamps[last] if last<len(timestamps) else timestamps[-1]+1/metadata['fps'],4)
            return events
        def emit_partial(scanned_frame):
            if not partial:return
            events=current_events()
            patterns=classify_regular_patterns(events,metadata)
            snapshot=json.loads(json.dumps(report))
            elapsed=max(.001,time.perf_counter()-start_time)
            snapshot.update(state='paused_preview',events=events,patterns=patterns,
                            scan_progress=dict(scanned_through_frame=max(-1,scanned_frame),frames_scanned=max(0,scanned_frame+1),
                                               total_frames=metadata['frames'],seconds_scanned=round(max(0,scanned_frame+1)/metadata['fps'],3)),
                            warnings=['Provisional pause preview: only the scanned portion is represented. Events may merge or change when scanning resumes.'],
                            performance=dict(analysis_fps=round(max(0,scanned_frame+1)/elapsed,2),
                                             detection_realtime_ratio=round(max(0,scanned_frame+1)/elapsed/metadata['fps'],2)))
            partial(folder,snapshot)
        for index in range(metadata['frames']):
            if pause_gate and not pause_gate.is_set():emit_partial(index-1)
            check_cancel(cancel,pause_gate)
            ok,frame=cap.read()
            if not ok: raise RuntimeError(f'Video ended unexpectedly at frame {index}.')
            timestamps.append(cap.get(cv2.CAP_PROP_POS_MSEC)/1000)
            matrix,stable=stabilizer.transform(frame)
            if not stable:
                unstable+=1
                continue
            for name,detector in detectors.items():
                spots=detector.detect(aligned_crop(frame,boxes[name],matrix))
                if spots:
                    best=max(spots,key=lambda s:s['contrast'])
                    observations[name].append(dict(region=name,frame=index,score=best['contrast'],bbox=best['box'],components=len(spots),transform=matrix.tolist()))
            if index%15==0:
                update(.65*index/metadata['frames'],f'Analyzing frame {index+1}/{metadata["frames"]}')
        detection_elapsed=time.perf_counter()-start_time
        events=current_events()
        use_pts=len(timestamps)>1 and all(b>a for a,b in zip(timestamps,timestamps[1:]))
        if use_pts:
            for event in events:
                event['start_seconds']=round(timestamps[event['start_frame']],4)
                event['peak_seconds']=round(timestamps[event['peak_frame']],4)
                last=event['last_frame']+1
                event['end_seconds']=round(timestamps[last] if last<len(timestamps) else timestamps[-1]+1/metadata['fps'],4)
        else:
            report['warnings'].append('Decoder did not return monotonic presentation timestamps; reported times use frame index / average FPS.')
        if use_pts and max(abs(timestamps[i]-i/metadata['fps']) for i in range(len(timestamps)))>.05:
            report['warnings'].append('Variable frame timing detected. Report times use presentation timestamps; exported clips use constant average FPS and may differ slightly.')
        if unstable:
            report['warnings'].append(f'{unstable} frames skipped because camera alignment failed. Zero candidates is not proof of no dripping.')
        if '_c' in path.stem.lower():
            report['warnings'].append('This appears to be a marked/re-encoded copy. Prefer the original for counting; do not add both copies together.')
        report['warnings'].append('No independent accuracy validation yet. Reflections, missed small/dark droplets, and merged bursts require human review.')
        report['warnings'].append('Clip audio is omitted. Sources are never modified.' if config.get('export_clips',False)
                                  else 'Event videos are not exported during scanning. Export selected events on request. Sources are never modified.')
        report['events']=events
        for event in events:
            duration=(event['last_frame']+1-event['start_frame'])/metadata['fps']
            event['kind']='continuous_activity' if duration>=config.get('continuous_seconds',.75) else 'candidate_drip_event'
            if event['kind']=='continuous_activity':
                event['notes']='Sustained visual activity; may be reflection or continuous fluid. Do not interpret as one drip.'
        for i,event in enumerate(events,1):
            event['id']=f'E{i:03d}'
            if config.get('export_clips',False):
                check_cancel(cancel,pause_gate)
                update(.65+.34*(i-1)/max(1,len(events)),f'Exporting event {i}/{len(events)}')
                export_event(cap,metadata,event,boxes[event['region']],clips,config,cancel,pause_gate=pause_gate)
        report['patterns']=classify_regular_patterns(events,metadata)
        total_elapsed=time.perf_counter()-start_time
        analysis_fps=metadata['frames']/max(.001,detection_elapsed)
        report['performance']=dict(detection_seconds=round(detection_elapsed,3),
                                   analysis_fps=round(analysis_fps,2),
                                   detection_realtime_ratio=round(analysis_fps/metadata['fps'],2),
                                   total_seconds=round(total_elapsed,3),overall_fps=round(metadata['frames']/total_elapsed,2),
                                   cpu_threads=cpu_threads,
                                   process_peak_working_set_mb=peak_memory_mb(),
                                   alignment_skipped_frames=unstable)
        report['state']='complete'
        save_json(folder/'results.json',report)
        write_csv(folder,events)
        write_patterns_csv(folder,report['patterns'])
        update(1,f'Complete: {len(events)} candidate events (review required)')
        return folder,report
    except Exception as exc:
        report['state']='cancelled' if isinstance(exc,Cancelled) else 'failed'
        report['error']=str(exc)
        save_json(folder/'results.json',report)
        raise
    finally:
        cap.release()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('videos',nargs='+')
    parser.add_argument('--config',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'output')
    parser.add_argument('--no-clips',action='store_true')
    args=parser.parse_args()
    cfg=json.loads(args.config.read_text(encoding='utf-8')) if args.config else json.loads(json.dumps(DEFAULT_CONFIG))
    if args.no_clips:cfg['export_clips']=False
    for video in args.videos:
        folder,report=analyze(video,args.output,cfg)
        print(json.dumps({'source':video,'output':str(folder),'candidates':len(report['events']),'performance':report['performance'],
                          'events':[(e['region'],e['peak_seconds']) for e in report['events']]}),flush=True)

if __name__=='__main__':
    main()
