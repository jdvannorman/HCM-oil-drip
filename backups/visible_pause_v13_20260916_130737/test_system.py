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
from drip_core import DEFAULT_CONFIG, ROOT, analyze, Cancelled, check_cancel, group_events, validate_config, open_video
import cv2
import numpy as np

class DetectorTests(unittest.TestCase):
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
            cfg.update(stabilize=False,export_clips=True,regions=[dict(name='Test',box=[0,0,1,1])])
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
            cancel=Event()
            cancel.set()
            with self.assertRaises(Cancelled):analyze(source,folder/'cancelled',cfg,cancel=cancel)
            cancelled=json.loads(next((folder/'cancelled').glob('*/results.json')).read_text())
            self.assertEqual(cancelled['state'],'cancelled')

if __name__=='__main__':unittest.main(verbosity=2)
