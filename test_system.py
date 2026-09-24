"""Focused regression checks for counting, source preservation, and video export."""
import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from threading import Event
from drip_core import (DEFAULT_CONFIG, ROOT, analyze, Cancelled, check_cancel, classify_regular_patterns,classify_location_groups,classify_visual_behavior,
                       group_events, validate_config, open_video)
import cv2
import numpy as np
from auto_roi import AreaTracker,locate_strip,strip_present

class DetectorTests(unittest.TestCase):
    def test_material_presence_gate_stops_and_reconfirms(self):
        background=np.full((360,640,3),(40,75,110),np.uint8)
        for y in range(0,360,8):cv2.line(background,(0,y),(639,y),(60,95,130),2)
        material=background.copy()
        quad=np.array([[(.25)*640,0],[(.65)*640,0],[(.80)*640,359],[(.10)*640,359]],np.int32)
        cv2.fillPoly(material,[quad],(195,160,140))
        edges=locate_strip(material)
        self.assertIsNotNone(edges)
        self.assertTrue(strip_present(material,edges))
        self.assertFalse(strip_present(background,edges))
        tracker=AreaTracker(10,dict(roi_presence_check_seconds=.2,roi_camera_check_seconds=10,
                                    roi_startup_seconds=1,roi_startup_stride=1))
        self.assertEqual(tracker.update(material,0)['status'],'tracking')
        tracker.update(material,1)
        self.assertIn(tracker.update(background,2)['status'],('held','no_material'))
        self.assertEqual(tracker.update(background,3)['status'],'no_material')
        self.assertEqual(tracker.update(material,4)['status'],'no_material')
        self.assertEqual(tracker.update(material,5)['status'],'material_confirming')
        self.assertEqual(tracker.update(material,7)['status'],'tracking')

    def test_regular_location_and_frequency_groups_are_separate(self):
        def event(index,time_seconds,x,region='Left'):
            return dict(id=f'E{index:03d}',region=region,start_frame=round(time_seconds*30),
                        peak_frame=round(time_seconds*30),last_frame=round(time_seconds*30),
                        start_seconds=time_seconds,peak_seconds=time_seconds,end_seconds=time_seconds+1/30,
                        peak_score=20+index,peak_source_bbox=[x,300,x+8,310],bbox=[1,1,5,6],components=1,
                        status='unreviewed',confirmed_drips=None,notes='')
        events=[event(i+1,t,100) for i,t in enumerate([0,5.05,10.0,15.02])]
        events += [event(i+5,t,420) for i,t in enumerate([1,7,13,19])]
        events += [event(i+9,t,700) for i,t in enumerate([2,4,13,29])]
        patterns=classify_regular_patterns(events,dict(width=1920,height=1080,fps=30))
        self.assertEqual(len(patterns),2)
        self.assertEqual(sorted(p['occurrences'] for p in patterns),[4,4])
        self.assertEqual(sum(e['pattern_class']=='regular' for e in events),8)
        self.assertTrue(all(e['pattern_class']=='unclassified' for e in events[-4:]))

    def test_smoothly_changing_frequency_is_grouped(self):
        times=[0,8,15,21,26]  # intervals 8, 7, 6, 5 seconds: speed is rising
        events=[]
        for i,seconds in enumerate(times,1):
            frame=round(seconds*30)
            events.append(dict(id=f'E{i:03d}',region='Right',start_frame=frame,peak_frame=frame,last_frame=frame,
                               start_seconds=seconds,peak_seconds=seconds,end_seconds=seconds+1/30,
                               peak_score=30,peak_source_bbox=[1300,800,1310,812],bbox=[1,1,5,6],components=1,
                               status='unreviewed',confirmed_drips=None,notes=''))
        patterns=classify_regular_patterns(events,dict(width=1920,height=1080,fps=30))
        self.assertEqual(len(patterns),1)
        self.assertEqual(patterns[0]['classification'],'regular_changing_frequency')
        self.assertEqual(patterns[0]['frequency_trend'],'frequency_increasing')
        self.assertGreater(patterns[0]['interval_start_seconds'],patterns[0]['interval_end_seconds'])

    def test_same_location_groups_even_when_timing_is_irregular(self):
        times=[0,1.2,17,18.1,52,119]
        events=[]
        for i,seconds in enumerate(times,1):
            events.append(dict(id=f'E{i:03d}',region='Left',peak_seconds=seconds,peak_score=20+i,
                               peak_source_bbox=[775+(i%2),930,785+(i%2),940],status='unreviewed'))
        events.append(dict(id='E007',region='Left',peak_seconds=4,peak_score=99,
                           peak_source_bbox=[1100,700,1110,710],status='unreviewed'))
        patterns=classify_regular_patterns(events,dict(width=1920,height=1080,fps=30))
        groups=classify_location_groups(events,dict(width=1920,height=1080,fps=30))
        self.assertEqual(patterns,[])
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0]['occurrences'],6)
        self.assertTrue(all(event['location_group_id']=='G01' for event in events[:6]))
        self.assertEqual(events[-1]['location_group_id'],'')

    def test_reflection_jitter_and_downward_motion_are_review_labels(self):
        events=[]
        for i in range(12):
            events.append(dict(id=f'E{i+1:03d}',region='Left',peak_seconds=i*2.7,peak_score=20,
                               peak_source_bbox=[775+(i%2),930,785+(i%2),940],positive_frames=1,status='unreviewed'))
        moving=dict(id='E013',region='Center',peak_seconds=2,peak_score=30,
                    peak_source_bbox=[1000,700,1010,710],first_source_bbox=[1000,680,1010,690],
                    last_source_bbox=[1002,700,1012,710],positive_frames=4,status='unreviewed')
        events.append(moving)
        groups=classify_location_groups(events,dict(width=1920,height=1080,fps=30))
        classify_visual_behavior(events,groups,dict(width=1920,height=1080,fps=30))
        self.assertEqual(groups[0]['visual_classification'],'likely_reflection_jitter')
        self.assertTrue(all(event['visual_classification']=='likely_reflection_jitter' for event in events[:12]))
        self.assertEqual(moving['visual_classification'],'possible_moving_droplet')

    def test_pause_gate_resumes_and_cancel_interrupts_pause(self):
        gate=Event()
        cancel=Event()
        resumed=Event()
        worker=threading.Thread(target=lambda:(check_cancel(cancel,gate),resumed.set()),daemon=True)
        worker.start()
        time.sleep(.2)
        self.assertFalse(resumed.is_set())
        gate.set();worker.join(1)
        self.assertTrue(resumed.is_set())

        gate.clear();cancel.set()
        with self.assertRaises(Cancelled):check_cancel(cancel,gate)

    def test_bursts_are_not_frame_counts(self):
        def obs(i):return dict(frame=i,region='Left',score=20,bbox=[2,2,8,8],components=2)
        events=group_events([obs(i) for i in [0,1,2,3,12,13]],30,.13)
        self.assertEqual(len(events),2)
        self.assertEqual(events[0]['positive_frames'],4)
        self.assertEqual(events[0]['max_components'],2)

    def test_invalid_roi_rejected(self):
        cfg=copy.deepcopy(DEFAULT_CONFIG)
        cfg['regions'][0]['box']=[.5,.5,.4,.6]
        with self.assertRaises(ValueError):validate_config(cfg)

    def test_static_bright_reflection_and_two_transient_spots(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'inspection') as tmp:
            folder=Path(tmp)
            source=folder/'synthetic.mp4'
            writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),30,(320,240))
            self.assertTrue(writer.isOpened())
            for i in range(75):
                frame=np.full((240,320,3),110,np.uint8)
                cv2.rectangle(frame,(10,10),(30,220),(210,210,210),-1)
                if i in [10,11,12,13]:cv2.circle(frame,(120,120+(i-10)*5),5,(240,240,240),-1)
                if i in [70,71,72,73,74]:cv2.circle(frame,(180,120+(i-70)*5),5,(240,240,240),-1)
                writer.write(frame)
            writer.release()
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            cfg=copy.deepcopy(DEFAULT_CONFIG)
            cfg.update(stabilize=False,export_clips=True,export_playback_speed=.25,
                       regions=[dict(name='Test',box=[0,0,1,1])])
            result,report=analyze(source,folder/'results',cfg)
            self.assertEqual(report['state'],'complete')
            self.assertEqual(len(report['events']),2)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),digest)
            for e in report['events']:
                self.assertEqual(e['status'],'unreviewed')
                self.assertIsNone(e['confirmed_drips'])
                self.assertGreaterEqual(e['clip_start_frame'],0)
                self.assertLessEqual(e['clip_end_frame_exclusive'],75)
                expected=e['clip_end_frame_exclusive']-e['clip_start_frame']
                for key in ['clip','closeup_clip']:
                    cap,meta=open_video(result/e[key])
                    decoded=0
                    try:
                        while True:
                            ok,frame=cap.read()
                            if not ok:break
                            decoded+=1
                    finally:cap.release()
                    self.assertEqual(decoded,expected)
                    self.assertAlmostEqual(meta['fps'],7.5,delta=.15)
                self.assertEqual(e['export_playback_speed'],.25)
                self.assertIn('0.25x',e['available_export_speeds'])
                self.assertIn('_0p25x',e['closeup_clip'])
            preview_gate=Event();preview_gate.set()
            previews=[]
            preview_cfg=copy.deepcopy(cfg);preview_cfg['export_clips']=False
            def preview_progress(value,message):
                if value>.1 and not previews:preview_gate.clear()
            def receive_preview(preview_folder,preview_report):
                previews.append((preview_folder,preview_report))
                preview_gate.set()
            analyze(source,folder/'pause_preview',preview_cfg,preview_progress,None,preview_gate,receive_preview)
            self.assertTrue(previews)
            self.assertEqual(previews[0][1]['state'],'paused_preview')
            self.assertGreater(previews[0][1]['scan_progress']['frames_scanned'],0)
            self.assertGreaterEqual(len(previews[0][1]['events']),1)
            cancel=Event()
            cancel.set()
            with self.assertRaises(Cancelled):analyze(source,folder/'cancelled',cfg,cancel=cancel)
            cancelled=json.loads(next((folder/'cancelled').glob('*/results.json')).read_text())
            self.assertEqual(cancelled['state'],'cancelled')

if __name__=='__main__':unittest.main(verbosity=2)
