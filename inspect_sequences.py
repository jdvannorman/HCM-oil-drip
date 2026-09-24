import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / '.deps'))
import cv2
from PIL import Image, ImageDraw
from inspect_videos import SOURCE

def sequence(name, box, step=6):
    cap = cv2.VideoCapture(str(SOURCE/name))
    fps = cap.get(cv2.CAP_PROP_FPS)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    idxs = list(range(0, frames, step))
    w,h=240,230
    sheet=Image.new('RGB',(w*7, (h+24)*((len(idxs)+6)//7)), '#14202b')
    d=ImageDraw.Draw(sheet)
    for i,f in enumerate(idxs):
        cap.set(cv2.CAP_PROP_POS_FRAMES,f)
        ok,frame=cap.read()
        if not ok:continue
        x1,y1,x2,y2=box
        im=Image.fromarray(cv2.cvtColor(frame[y1:y2,x1:x2],cv2.COLOR_BGR2RGB))
        im.thumbnail((w,h))
        x,y=(i%7)*w,(i//7)*(h+24)
        sheet.paste(im,(x,y))
        d.text((x+3,y+h+3),f'{f} / {f/fps:.3f}s',fill='white')
    cap.release()
    sheet.save(Path('inspection')/(Path(name).stem+'_sequence.jpg'))

if __name__=='__main__':
    sequence('2026_09_04__10_26_42.mp4',(570,810,810,1040),6)
    sequence('2026_09_04__10_26_56.mp4',(1190,710,1430,940),3)
    sequence('2026_09_15__09_47_49.mp4',(610,820,850,1050),12)
