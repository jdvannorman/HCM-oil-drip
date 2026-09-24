from pathlib import Path
import json
import sys
from PIL import Image, ImageDraw

def build(root):
    for path in Path(root).glob('*/results.json'):
        report=json.loads(path.read_text(encoding='utf-8'))
        events=[e for e in report['events'] if 'snapshot' in e]
        if not events: continue
        sheet=Image.new('RGB',(1320,290*((len(events)+3)//4)),'#172331')
        draw=ImageDraw.Draw(sheet)
        for i,e in enumerate(events):
            im=Image.open(path.parent/e['snapshot']).convert('RGB')
            im.thumbnail((330,260))
            x,y=i%4*330,i//4*290
            sheet.paste(im,(x,y))
            draw.text((x+4,y+263),f"{e['id']} {e['region']} {e['peak_seconds']:.3f}s | score {e['peak_score']}",fill='white')
        target=path.parent/'events_sheet.jpg'
        sheet.save(target)
        print(str(target))

if __name__=='__main__':build(sys.argv[1])
