"""Automatic ROI analysis; persisted geometry is shared by review and exports."""
import copy
import csv
import json
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np
from auto_roi import AreaTracker,regions_from_edges,rectify

def analyze_auto(path,output_root,config,progress,cancel):
    from drip_core import (ROOT,open_video,check_cancel,save_json,gray_blur,RegionDetector,configure_cpu,
                           group_events,export_event,write_csv,peak_memory_mb,Cancelled)
    cpu_threads=configure_cpu(config)
    cap,meta=open_video(path)
    path=Path(path).resolve()
    folder=Path(output_root or ROOT/'output')/(path.stem+'_auto_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:5])
    folder.mkdir(parents=True)
    (folder/'clips').mkdir()
    report=dict(version=2,state='running',source=str(path),metadata=meta,config=config,events=[],warnings=[],performance={},
                region_timeline='region_timeline.json',count_definition='Candidate coolant bursts, not confirmed droplets.',
                time_basis='Source PTS where available; clip timing uses constant average FPS.')
    save_json(folder/'results.json',report)
    started=time.perf_counter()
    names=[r['name'] for r in config['regions']]
    canonical=copy.deepcopy(config)
    canonical['regions']=[dict(name=n,box=[0,0,1,1]) for n in names]
    def detectors_for(samples):
        return {n:RegionDetector(n,(0,0,224,224),np.median(np.stack(samples[n]),axis=0).astype(np.float32),canonical) for n in names}
    def report_progress(p,msg):
        if progress:progress(p,msg)
    timeline=[]
    try:
        tracker=AreaTracker(meta['fps'],config)
        samples={n:[] for n in names}
        report_progress(0,'Locating strip edges and building aligned backgrounds…')
        for index in range(min(meta['frames'],30)):
            check_cancel(cancel)
            ok,frame=cap.read()
            if not ok:raise RuntimeError('Could not decode calibration frames.')
            state=tracker.update(frame,index)
            if state['status']!='tracking':continue
            regions=regions_from_edges(state['edges'],config['regions'])
            for r in regions:samples[r['name']].append(gray_blur(rectify(frame,r)[0]))
        if not all(len(v)>=3 for v in samples.values()):
            raise ValueError('Automatic strip location is unreliable at the start. Use Manual areas or a clearer start frame; no automatic drip count was produced.')
        detectors=detectors_for(samples)
        tracker=AreaTracker(meta['fps'],config)
        observations={n:[] for n in names}
        cap.set(cv2.CAP_PROP_POS_FRAMES,0)
        times=[]
        last_edges=None
        warming=None
        was_lost=False
        for index in range(meta['frames']):
            check_cancel(cancel)
            ok,frame=cap.read()
            if not ok:raise RuntimeError(f'Cannot decode frame {index}.')
            times.append(cap.get(cv2.CAP_PROP_POS_MSEC)/1000)
            state=tracker.update(frame,index)
            regions=regions_from_edges(state['edges'],config['regions']) if state['edges'] else []
            record=dict(frame=index,status=state['status'],geometry_check=state.get('geometry_check','full'),
                        quality=state['edges']['quality'] if state['edges'] else 0,regions=regions)
            timeline.append(record)
            # Held geometry is shown for context but never used to count.
            if state['status']!='tracking':
                was_lost=True
                continue
            delta=max(abs(np.polyval(state['edges'][s],.86)-np.polyval(last_edges[s],.86)) for s in ('left','right')) if last_edges else 0
            if delta>.012 or was_lost:
                warming={n:[] for n in names}
            was_lost=False
            last_edges=copy.deepcopy(state['edges'])
            crops={r['name']:rectify(frame,r) for r in regions}
            if warming is not None:
                record['status']='recalibrating'
                for n in names:warming[n].append(gray_blur(crops[n][0]))
                if len(warming[names[0]])>=8:
                    detectors=detectors_for(warming)
                    warming=None
                continue
            for r in regions:
                name=r['name']
                crop,inverse=crops[name]
                spots=detectors[name].detect(crop,r.get('polygon'))
                if not spots:continue
                best=max(spots,key=lambda s:s['contrast'])
                x0,y0,x1,y1=best['box']
                corners=np.array([[[x0,y0],[x1,y0],[x1,y1],[x0,y1]]],np.float32)
                raw=cv2.perspectiveTransform(corners,inverse)[0]
                observations[name].append(dict(frame=index,region=name,score=best['contrast'],bbox=best['box'],components=len(spots),
                     peak_source_bbox=[float(raw[:,0].min()),float(raw[:,1].min()),float(raw[:,0].max()),float(raw[:,1].max())],
                     peak_region=r))
            if index%15==0:report_progress(.65*index/meta['frames'],f'Auto areas: frame {index+1}/{meta["frames"]}')
        events=[]
        for name,items in observations.items():
            # Do not merge positives across skipped/recalibration gaps.
            chunks=[]
            for obs in items:
                if not chunks or any(timeline[i]['status']!='tracking' for i in range(chunks[-1][-1]['frame']+1,obs['frame'])):chunks.append([])
                chunks[-1].append(obs)
            for chunk in chunks:events.extend(group_events(chunk,meta['fps'],config['merge_gap_seconds']))
        events.sort(key=lambda e:(e['start_frame'],e['region']))
        pts=len(times)>1 and all(b>a for a,b in zip(times,times[1:]))
        for i,e in enumerate(events,1):
            e['id']=f'E{i:03d}'
            e['kind']='continuous_activity' if (e['last_frame']+1-e['start_frame'])/meta['fps']>=config.get('continuous_seconds',.75) else 'candidate_drip_event'
            if pts:
                e['start_seconds']=round(times[e['start_frame']],4)
                e['peak_seconds']=round(times[e['peak_frame']],4)
                end=e['last_frame']+1
                e['end_seconds']=round(times[end] if end<len(times) else times[-1]+1/meta['fps'],4)
        detection_time=time.perf_counter()-started
        save_json(folder/'region_timeline.json',timeline)
        counts=dict(Counter(t['status'] for t in timeline))
        check_counts=dict(Counter(t.get('geometry_check','full') for t in timeline))
        report['tracking']=dict(frame_counts=counts,geometry_checks=check_counts,
                                full_edge_checks=tracker.full_checks,camera_checks=tracker.camera_checks,
                                usable_frames=counts.get('tracking',0),total_frames=len(timeline))
        skipped=len(timeline)-counts.get('tracking',0)
        report['warnings']=[f'{skipped} frames not analyzed while areas were held, lost, or recalibrating. Zero events is not proof of no dripping.',
                            'Automatic edges are camera-specific estimates, not measured coil width. Inspect the preview. Event counts require human review.',
                            'Clips use constant average FPS and omit audio. Sources are never modified.']
        if not pts:report['warnings'].append('Presentation timestamps unavailable; times use frame index / average FPS.')
        if path.stem.lower().endswith('_c'):report['warnings'].append('Marked/re-encoded copy: painted circles can affect localization and detection. Prefer originals.')
        if pts and max(abs(times[i]-i/meta['fps']) for i in range(len(times)))>.05:report['warnings'].append('Variable frame timing: report timestamps and constant-rate clip playback may differ slightly.')
        report['events']=events
        from drip_core import pixel_box
        for i,e in enumerate(events):
            check_cancel(cancel)
            report_progress(.65+.34*i/max(1,len(events)),f'Exporting auto-area event {i+1}/{len(events)}')
            box=pixel_box(e['peak_region'],meta['width'],meta['height'])
            export_event(cap,meta,e,box,folder/'clips',config,cancel,timeline=timeline)
        report.update(state='complete',performance=dict(detection_seconds=round(detection_time,3),analysis_fps=round(meta['frames']/detection_time,2),
                       total_seconds=round(time.perf_counter()-started,3),cpu_threads=cpu_threads,
                       process_peak_working_set_mb=peak_memory_mb(),alignment_skipped_frames=skipped))
        save_json(folder/'results.json',report)
        write_csv(folder,events)
        report_progress(1,f'Complete: {len(events)} candidates; {skipped} frames not analyzed')
        return folder,report
    except Exception as exc:
        report.update(state='cancelled' if isinstance(exc,Cancelled) else 'failed',error=str(exc))
        save_json(folder/'results.json',report)
        if timeline:save_json(folder/'region_timeline.json',timeline)
        raise
    finally:cap.release()
