"""Edge-following regions for the fixed side-trimmer camera (not a trained model)."""
import copy
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'.deps'))
import cv2
import numpy as np

def locate_strip(frame,debug=False):
    """Return normalized x=a*y+b boundary lines with a geometric quality score."""
    height,width=frame.shape[:2]
    small=cv2.resize(frame,(640,round(height*640/width)))
    h,w=small.shape[:2]
    gray=cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
    edges=cv2.Canny(cv2.GaussianBlur(gray,(3,3),0),35,100)
    y0,y1=int(h*.67),int(h*.98)
    edges[:y0]=0
    edges[y1:]=0
    b,g,r=cv2.split(small.astype(np.float32))
    blue=cv2.GaussianBlur(b-r,(7,7),0)
    dilated=cv2.dilate(edges,np.ones((3,3),np.uint8))
    yy=np.linspace(y0+4,y1-4,45).astype(int)
    values=gray.astype(np.float32)
    texture_map=np.sqrt(np.maximum(0,cv2.blur(values*values,(11,5))-cv2.blur(values,(11,5))**2))
    candidates={}
    for side,direction,start,stop,slopes in [('Left',1,.10,.47,np.linspace(-.75,.03,40)),('Right',-1,.53,.92,np.linspace(-.03,.75,40))]:
        centers,aa=np.meshgrid(np.arange(w*start,w*stop,2),slopes)
        aa=aa.ravel()
        bb=centers.ravel()-aa*((y0+y1)/2)
        xx=np.rint(aa[:,None]*yy+bb[:,None]).astype(int)
        valid=(xx.min(axis=1)>=18)&(xx.max(axis=1)<w-18)
        xx=np.clip(xx,18,w-19)
        inside=xx+direction*12
        outside=xx-direction*12
        delta=blue[yy,inside]-blue[yy,outside]
        contrast=np.median(delta,axis=1)
        inner_blue=np.median(blue[yy,inside],axis=1)
        texture=np.median(texture_map[yy,outside]-texture_map[yy,inside],axis=1)
        support=np.mean((delta>5)|(texture_map[yy,outside]-texture_map[yy,inside]>7),axis=1)
        edge_support=np.mean(dilated[yy,xx]>0,axis=1)
        quality=.25*np.clip(contrast/30,0,1)+.15*np.clip(texture/14,0,1)+.25*support+.35*edge_support
        quality[~valid | (inner_blue<4) | (contrast<3) | (support<.60)]=-1
        best=int(np.argmax(quality))
        if debug:print(side,float(quality[best]),float(contrast[best]),float(texture[best]),float(support[best]))
        if quality[best]<.53:return None
        candidates[side]=(float(quality[best]),[float(aa[best]*h/w),float(bb[best]/w)])
    left=candidates['Left']
    right=candidates['Right']
    for y in [.70,.98]:
        lx=left[1][0]*y+left[1][1]
        rx=right[1][0]*y+right[1][1]
        if not (.08<lx<rx<.94 and .22<rx-lx<.78):return None
    quality=min(left[0],right[0])
    if quality<.48:return None
    return dict(left=left[1],right=right[1],quality=round(quality,3))

def regions_from_edges(edges, templates):
    """Keep vertical extent; follow perspective edges with a fixed image-width band."""
    result=[]
    for template in templates:
        region=copy.deepcopy(template)
        side=region['name']
        if side not in ('Left','Right'):raise ValueError('Automatic areas require Left and/or Right names.')
        a,b=edges[side.lower()]
        _,top,_,bottom=region['box']
        # Offset clear of the brightest edge; band follows the same physical side.
        inset=.018
        span=min(np.polyval(edges['right'],y)-np.polyval(edges['left'],y) for y in (top,bottom))
        band=min(.105,.4*span-inset)
        if band<=.015:raise ValueError('Strip is too narrow for non-overlapping automatic regions.')
        values=[a*y+b for y in (top,bottom)]
        if side=='Left':
            points=[[values[0]+inset,top],[values[0]+inset+band,top],
                    [values[1]+inset+band,bottom],[values[1]+inset,bottom]]
        else:
            points=[[values[0]-inset-band,top],[values[0]-inset,top],
                    [values[1]-inset,bottom],[values[1]-inset-band,bottom]]
        if any(not 0<=x<=1 for x,y in points):raise ValueError('Automatic area extends outside the camera view.')
        x0=min(p[0] for p in points)
        x1=max(p[0] for p in points)
        region['box']=[x0,top,x1,bottom]
        region['polygon']=[[(x-x0)/(x1-x0),(y-top)/(bottom-top)] for x,y in points]
        result.append(region)
    return result

def region_quad(region, width, height):
    x0,y0,x1,y1=region['box']
    return ((np.array(region['polygon'],np.float32)*(x1-x0,y1-y0)+(x0,y0))*(width,height)).astype(np.float32)

def rectify(frame, region):
    """Map the moving quadrilateral to a constant-sized detector image."""
    quad=region_quad(region,frame.shape[1],frame.shape[0])
    dst=np.array([[0,0],[223,0],[223,223],[0,223]],np.float32)
    matrix=cv2.getPerspectiveTransform(quad,dst)
    return cv2.warpPerspective(frame,matrix,(224,224)),cv2.getPerspectiveTransform(dst,quad)

class AreaTracker:
    """Smooth valid edges, suppress unsafe jumps, hold briefly then mark lost."""
    def __init__(self,fps):
        self.fps=fps
        self.last=None
        self.last_good=None
        self.pending=None
        self.pending_count=0
        self.last_index=None

    def update(self,frame,index):
        if self.last_index is not None and index<=self.last_index:
            self.last=None; self.last_good=None; self.pending=None; self.pending_count=0
        self.last_index=index
        found=locate_strip(frame)
        if found:
            jump=max(abs(np.polyval(found[s],.86)-np.polyval(self.last[s],.86)) for s in ('left','right')) if self.last else 0
            if jump>.025:
                consistent=self.pending is not None and max(abs(np.polyval(found[s],.86)-np.polyval(self.pending[s],.86)) for s in ('left','right'))<.012
                self.pending_count=self.pending_count+1 if consistent else 1
                self.pending=found
                if self.pending_count<3:found=None
                else:self.last=None
            else:self.pending=None; self.pending_count=0
        if found:
            if self.last:
                for side in ('left','right'):
                    found[side]=(.3*np.array(found[side])+.7*np.array(self.last[side])).tolist()
            self.last=found
            self.last_good=index
            return dict(status='tracking',edges=copy.deepcopy(found))
        if self.last is not None and (index-self.last_good)/self.fps<=.5:
            return dict(status='held',edges=copy.deepcopy(self.last))
        return dict(status='lost',edges=None)
