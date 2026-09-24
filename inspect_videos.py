"""Read source videos and create local contact sheets; never modify sources."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / '.deps'))
import cv2
import json
from PIL import Image, ImageDraw

SOURCE = Path(r'C:\Users\sam_lee\OneDrive\WK\Problem Solving\HM Coolant Stain\SIDE TRIMMER')
NAMES = ['2026_08_31__06_33_24.mp4', '2026_08_31__06_34_13.mp4',
         '2026_09_04__10_26_42.mp4', '2026_09_04__10_26_42_C.mp4',
         '2026_09_04__10_26_56.mp4', '2026_09_04__10_26_56_C.mp4',
         '2026_09_15__09_47_49.mp4', '2026_08_31 6_33_07.mp4']

def inspect():
    out = Path(__file__).parent / 'inspection'
    out.mkdir(exist_ok=True)
    records = []
    for name in NAMES:
        cap = cv2.VideoCapture(str(SOURCE / name))
        if not cap.isOpened():
            records.append({'name': name, 'error': 'cannot open'})
            continue
        fps = cap.get(cv2.CAP_PROP_FPS)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        record = dict(name=name, path=str(SOURCE / name), fps=fps, frames=n,
                      width=int(cap.get(3)), height=int(cap.get(4)), duration=n/fps)
        sheet = Image.new('RGB', (1280, 780), '#18212b')
        draw = ImageDraw.Draw(sheet)
        draw.text((10, 5), name + f' | {record["width"]}x{record["height"]} | {fps:.2f} fps | {n/fps:.2f}s', fill='white')
        for i, frac in enumerate([0.05, .33, .66, .93]):
            idx = min(n-1, int(n*frac))
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                continue
            im = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            im.thumbnail((640, 350))
            x, y = (i%2)*640, 35+(i//2)*370
            sheet.paste(im, (x, y))
            draw.text((x+8, y+350), f'Frame {idx} | {idx/fps:.3f}s', fill='white')
            if i == 1:
                Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).save(out / (Path(name).stem + '_full.png'))
        cap.release()
        sheet.save(out / (Path(name).stem + '_sheet.jpg'))
        records.append(record)
        print(json.dumps(record), flush=True)
    (out/'metadata.json').write_text(json.dumps(records, indent=2), encoding='utf-8')

if __name__ == '__main__':
    inspect()
