"""Automatic ROI analysis; persisted geometry is shared by review and exports."""
import copy
import csv
import json
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np
from auto_roi import AreaTracker,regions_from_edges,rectify

def select_cpu_threads_for_video(path,config,cancel=None,pause_gate=None):
    """Benchmark the actual decode/tracking/region workload once per computer."""
    from drip_core import (ROOT,CPU_IDENTIFIER,LOGICAL_CPUS,DEFAULT_CPU_THREADS,open_video,check_cancel,
                           gray_blur,save_json,auto_select_cpu_threads)
    candidates=sorted(set([max(2,round(LOGICAL_CPUS*.35)),DEFAULT_CPU_THREADS,max(2,LOGICAL_CPUS-2)]))
    profile_path=ROOT/'cpu_tuning.json'
    signature={'cpu_identifier':CPU_IDENTIFIER,'logical_cpus':LOGICAL_CPUS,'opencv':cv2.__version__,
               'algorithm':'actual_auto_pipeline_v2','candidates':candidates}
    if profile_path.exists():
        try:
            saved=json.loads(profile_path.read_text(encoding='utf-8'))
            if all(saved.get(key)==value for key,value in signature.items()) and saved.get('selected') in candidates:
                return int(saved['selected'])
        except (OSError,ValueError,TypeError):pass
    scores={thread:[] for thread in candidates}
    orders=(candidates,list(reversed(candidates)))
    try:
        for order in orders:
            for threads in order:
                check_cancel(cancel,pause_gate)
                cv2.setNumThreads(threads)
                cap,meta=open_video(path)
                tracker=AreaTracker(meta['fps'],config)
                backgrounds={}
                processed=0
                began=time.perf_counter()
                try:
                    for index in range(min(meta['frames'],120)):
                        check_cancel(cancel,pause_gate)
                        ok,frame=cap.read()
                        if not ok:break
                        state=tracker.update(frame,index)
                        if not state.get('edges'):continue
                        regions=regions_from_edges(state['edges'],config['regions'])
                        for region in regions:
                            gray=gray_blur(rectify(frame,region)[0])
                            background=backgrounds.setdefault(region['name'],gray.copy())
                            diff=gray-background
                            local=diff-cv2.GaussianBlur(diff,(0,0),5)
                            mask=((local>config['threshold'])&(diff>config['threshold'])).astype(np.uint8)*255
                            mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((2,2),np.uint8))
                            cv2.connectedComponentsWithStats(mask)
                        processed+=1
                finally:cap.release()
                if processed<5:raise ValueError('Not enough stable automatic-area frames for CPU tuning.')
                scores[threads].append(time.perf_counter()-began)
        averages={str(thread):round(float(np.mean(values)),6) for thread,values in scores.items()}
        selected=min(candidates,key=lambda thread:averages[str(thread)])
        save_json(profile_path,{**signature,'selected':selected,'seconds':averages,
                                'sample_frames_per_trial':120,'trials_per_candidate':2,
                                'created_at':time.strftime('%Y-%m-%dT%H:%M:%S')})
        return selected
    except Exception:
        return auto_select_cpu_threads()

def decoded_frames(cap,total):
    """Decode frame N+1 while the caller processes frame N."""
    def read_one():
        ok,frame=cap.read()
        return ok,frame,cap.get(cv2.CAP_PROP_POS_MSEC)/1000
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix='video-decode') as pool:
        pending=pool.submit(read_one)
        for index in range(total):
            ok,frame,pts=pending.result()
            if index+1<total:pending=pool.submit(read_one)
            yield index,ok,frame,pts

def analyze_auto(path,output_root,config,progress,cancel,pause_gate=None,partial=None):
    from drip_core import (ROOT,open_video,check_cancel,save_json,gray_blur,RegionDetector,configure_cpu,
                           group_events,classify_regular_patterns,classify_location_groups,export_event,
                           write_csv,write_patterns_csv,write_location_groups_csv,peak_memory_mb,Cancelled)
    if config.get('cpu_threads','auto')=='auto':
        if progress:progress(0,'Auto-selecting the fastest CPU thread count (first run only)…')
        config['resolved_cpu_threads']=select_cpu_threads_for_video(path,config,cancel,pause_gate)
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
    def emit_empty_partial():
        if not partial:return
        snapshot=copy.deepcopy(report)
        snapshot.update(state='paused_preview',events=[],region_timeline=None,
                        scan_progress=dict(scanned_through_frame=-1,frames_scanned=0,total_frames=meta['frames'],seconds_scanned=0),
                        warnings=['Paused during initial calibration. No detection frames have been scanned yet.'],performance={})
        partial(folder,snapshot)
    timeline=[]
    try:
        tracker=AreaTracker(meta['fps'],config)
        samples={n:[] for n in names}
        report_progress(0,'Locating strip edges and building aligned backgrounds…')
        for index in range(min(meta['frames'],30)):
            if pause_gate and not pause_gate.is_set():emit_empty_partial()
            check_cancel(cancel,pause_gate)
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
        def current_events():
            events=[]
            for name,items in observations.items():
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
            return events
        def emit_partial(scanned_frame):
            if not partial:return
            events=current_events()
            patterns=classify_regular_patterns(events,meta)
            location_groups=classify_location_groups(events,meta)
            snapshot=copy.deepcopy(report)
            elapsed=max(.001,time.perf_counter()-started)
            snapshot.update(state='paused_preview',events=events,patterns=patterns,location_groups=location_groups,region_timeline=None,
                            scan_progress=dict(scanned_through_frame=max(-1,scanned_frame),frames_scanned=max(0,scanned_frame+1),
                                               total_frames=meta['frames'],seconds_scanned=round(max(0,scanned_frame+1)/meta['fps'],3)),
                            warnings=['Provisional pause preview: only the scanned portion is represented. Events may merge or change when scanning resumes.'],
                            performance=dict(analysis_fps=round(max(0,scanned_frame+1)/elapsed,2),
                                             detection_realtime_ratio=round(max(0,scanned_frame+1)/elapsed/meta['fps'],2)))
            partial(folder,snapshot)
        for index,ok,frame,pts in decoded_frames(cap,meta['frames']):
            if pause_gate and not pause_gate.is_set():emit_partial(index-1)
            check_cancel(cancel,pause_gate)
            if not ok:raise RuntimeError(f'Cannot decode frame {index}.')
            times.append(pts)
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
        events=current_events()
        patterns=classify_regular_patterns(events,meta)
        location_groups=classify_location_groups(events,meta)
        pts=len(times)>1 and all(b>a for a,b in zip(times,times[1:]))
        detection_time=time.perf_counter()-started
        detection_fps=meta['frames']/max(.001,detection_time)
        save_json(folder/'region_timeline.json',timeline)
        counts=dict(Counter(t['status'] for t in timeline))
        check_counts=dict(Counter(t.get('geometry_check','full') for t in timeline))
        report['tracking']=dict(frame_counts=counts,geometry_checks=check_counts,
                                full_edge_checks=tracker.full_checks,camera_checks=tracker.camera_checks,
                                usable_frames=counts.get('tracking',0),total_frames=len(timeline))
        skipped=len(timeline)-counts.get('tracking',0)
        report['warnings']=[f'{skipped} frames not analyzed while areas were held, lost, or recalibrating. Zero events is not proof of no dripping.',
                            'Automatic edges are camera-specific estimates, not measured coil width. Inspect the preview. Event counts require human review.',
                            'Event videos are not exported during scanning. Select an event and request export when needed. Sources are never modified.']
        if config.get('export_clips',False):report['warnings'][-1]='Clips use constant average FPS and omit audio. Sources are never modified.'
        if detection_fps<meta['fps']:
            report['warnings'].append(f'Detection ran at {detection_fps:.1f} fps, below this video’s {meta["fps"]:.1f} fps. Close other heavy programs or use Fast mode.')
        if not pts:report['warnings'].append('Presentation timestamps unavailable; times use frame index / average FPS.')
        if path.stem.lower().endswith('_c'):report['warnings'].append('Marked/re-encoded copy: painted circles can affect localization and detection. Prefer originals.')
        if pts and max(abs(times[i]-i/meta['fps']) for i in range(len(times)))>.05:report['warnings'].append('Variable frame timing: report timestamps and constant-rate clip playback may differ slightly.')
        report['events']=events
        report['patterns']=patterns
        report['location_groups']=location_groups
        if config.get('export_clips',False):
            from drip_core import pixel_box
            for i,e in enumerate(events):
                check_cancel(cancel,pause_gate)
                report_progress(.65+.34*i/max(1,len(events)),f'Exporting auto-area event {i+1}/{len(events)}')
                box=pixel_box(e['peak_region'],meta['width'],meta['height'])
                export_event(cap,meta,e,box,folder/'clips',config,cancel,timeline=timeline,pause_gate=pause_gate)
        total_elapsed=time.perf_counter()-started
        analysis_fps=detection_fps
        report.update(state='complete',performance=dict(detection_seconds=round(detection_time,3),analysis_fps=round(analysis_fps,2),
                       detection_realtime_ratio=round(analysis_fps/meta['fps'],2),
                       total_seconds=round(total_elapsed,3),overall_fps=round(meta['frames']/total_elapsed,2),cpu_threads=cpu_threads,
                       process_peak_working_set_mb=peak_memory_mb(),alignment_skipped_frames=skipped))
        save_json(folder/'results.json',report)
        write_csv(folder,events)
        write_patterns_csv(folder,patterns)
        write_location_groups_csv(folder,location_groups)
        report_progress(1,f'Complete: {len(events)} candidates; {skipped} frames not analyzed')
        return folder,report
    except Exception as exc:
        report.update(state='cancelled' if isinstance(exc,Cancelled) else 'failed',error=str(exc))
        save_json(folder/'results.json',report)
        if timeline:save_json(folder/'region_timeline.json',timeline)
        raise
    finally:cap.release()
