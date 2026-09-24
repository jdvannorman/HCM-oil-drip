"""Decode every frame of exported clips and check the recorded boundaries."""
import json
from pathlib import Path
from drip_core import ROOT,open_video,save_json

def main():
    samples=json.loads((ROOT/'output'/'sample_index.json').read_text(encoding='utf-8'))
    checked=0
    frames=0
    for sample in samples:
        folder=Path(sample['folder'])
        if not folder.is_absolute():folder=ROOT/folder
        report=json.loads((folder/'results.json').read_text(encoding='utf-8'))
        assert report['state']=='complete'
        assert len(report['events'])==sample['candidates']
        for event in report['events']:
            expected=event['clip_end_frame_exclusive']-event['clip_start_frame']
            for key in ['clip','closeup_clip']:
                cap,meta=open_video(folder/event[key])
                n=0
                try:
                    while True:
                        ok,frame=cap.read()
                        if not ok:break
                        n+=1
                finally:cap.release()
                assert n==expected,(event['id'],key,n,expected)
                assert abs(meta['fps']-report['metadata']['fps'])<.02
                checked+=1
                frames+=n
    result={'state':'passed','source_videos':len(samples),'exported_clips_fully_decoded':checked,'exported_frames_decoded':frames}
    save_json(ROOT/'output'/'verification.json',result)
    print(json.dumps(result))

if __name__=='__main__':main()
