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

DEFAULT_STRIP_POLYGONS={
    'Left': [[.04,.72],[.30,.72],[.30,.98],[.04,.98]],
    'Center': [[.34,.72],[.66,.72],[.66,.98],[.34,.98]],
    'Right': [[.70,.72],[.96,.72],[.96,.98],[.70,.98]],
}

def source_to_strip(points, edges):
    """Convert normalized image points to (across-strip, image-y) coordinates."""
    result=[]
    dy=edges.get('camera_shift',[0,0])[1]
    for x,y in points:
        left=float(np.polyval(edges['left'],y))
        right=float(np.polyval(edges['right'],y))
        if right-left<.05:raise ValueError('The strip is too narrow at this height.')
        result.append([(x-left)/(right-left),y-dy])
    return result

def strip_to_source(points, edges):
    """Project (across-strip, image-y) points onto the current strip edges."""
    dy=edges.get('camera_shift',[0,0])[1]
    result=[]
    for u,base_y in points:
        y=base_y+dy
        result.append([float(np.polyval(edges['left'],y)+u*(np.polyval(edges['right'],y)-np.polyval(edges['left'],y))),y])
    return result

def regions_from_edges(edges, templates):
    """Project arbitrary strip-relative polygons onto the current camera frame."""
    result=[]
    for template in templates:
        region=copy.deepcopy(template)
        strip_polygon=region.get('strip_polygon') or DEFAULT_STRIP_POLYGONS.get(region['name'])
        if not strip_polygon:
            # Older/custom settings can still be opened. Their image polygon is
            # converted once using the current edges, then follows the strip.
            x0,y0,x1,y1=region['box']
            local=region.get('polygon',[[0,0],[1,0],[1,1],[0,1]])
            image_points=[[x0+p[0]*(x1-x0),y0+p[1]*(y1-y0)] for p in local]
            strip_polygon=source_to_strip(image_points,edges)
        if len(strip_polygon)<3:raise ValueError('An automatic area needs at least three points.')
        if any(not (0<=u<=1 and 0<=v<=1) for u,v in strip_polygon):
            raise ValueError(f'{region["name"]}: the contour must stay inside the detected strip.')
        points=strip_to_source(strip_polygon,edges)
        if any(not (0<=x<=1 and 0<=y<=1) for x,y in points):raise ValueError('Automatic area extends outside the camera view.')
        x0=min(p[0] for p in points)
        x1=max(p[0] for p in points)
        y0=min(p[1] for p in points)
        y1=max(p[1] for p in points)
        if x1-x0<.005 or y1-y0<.005:raise ValueError(f'{region["name"]}: automatic area is too small.')
        region['strip_polygon']=copy.deepcopy(strip_polygon)
        region['box']=[x0,y0,x1,y1]
        region['polygon']=[[(x-x0)/(x1-x0),(y-y0)/(y1-y0)] for x,y in points]
        result.append(region)
    return result

def region_quad(region, width, height):
    x0,y0,x1,y1=region['box']
    return ((np.array(region['polygon'],np.float32)*(x1-x0,y1-y0)+(x0,y0))*(width,height)).astype(np.float32)

def rectify(frame, region):
    """Map an arbitrary moving polygon's bounding box to a detector image."""
    x0,y0,x1,y1=region['box']
    h,w=frame.shape[:2]
    xa,ya=max(0,int(np.floor(x0*w))),max(0,int(np.floor(y0*h)))
    xb,yb=min(w,int(np.ceil(x1*w))),min(h,int(np.ceil(y1*h)))
    if xb-xa<2 or yb-ya<2:raise ValueError('Automatic area is too small to rectify.')
    crop=cv2.resize(frame[ya:yb,xa:xb],(224,224),interpolation=cv2.INTER_LINEAR)
    inverse=np.array([[(xb-xa)/224,0,xa],[0,(yb-ya)/224,ya],[0,0,1]],np.float32)
    return crop,inverse

class AreaTracker:
    """Smooth valid edges, suppress unsafe jumps, hold briefly then mark lost."""
    def __init__(self,fps):
        self.fps=fps
        self.last=None
        self.last_good=None
        self.pending=None
        self.pending_count=0
        self.last_index=None
        self.camera_ref=None
        self.camera_points=None
        self.camera_shift=np.zeros(2,np.float32)

    def estimate_camera_shift(self,frame):
        """Estimate small x/y camera translation from fixed machinery."""
        height,width=frame.shape[:2]
        small=cv2.resize(frame,(640,round(height*640/width)))
        gray=cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
        if self.camera_ref is None:
            self.camera_ref=gray
            h,w=gray.shape
            mask=np.zeros_like(gray)
            mask[:int(h*.62),:]=255
            mask[:, :int(w*.18)]=255
            mask[:, int(w*.84):]=255
            self.camera_points=cv2.goodFeaturesToTrack(gray,220,.025,7,mask=mask)
            return [0.,0.]
        if self.camera_points is None or len(self.camera_points)<8:return self.camera_shift.tolist()
        moved,status,errors=cv2.calcOpticalFlowPyrLK(self.camera_ref,gray,self.camera_points,None,winSize=(25,25),maxLevel=3)
        if moved is None:return self.camera_shift.tolist()
        good=(status.ravel()==1)&(errors.ravel()<40)
        if good.sum()<8:return self.camera_shift.tolist()
        matrix,inliers=cv2.estimateAffinePartial2D(self.camera_points[good],moved[good],method=cv2.RANSAC,ransacReprojThreshold=1.8)
        if matrix is None or inliers is None or inliers.sum()<8:return self.camera_shift.tolist()
        scale=float(np.hypot(matrix[0,0],matrix[1,0]))
        angle=float(np.degrees(np.arctan2(matrix[1,0],matrix[0,0])))
        if not (.97<scale<1.03) or abs(angle)>2.0:return self.camera_shift.tolist()
        shift=np.array([matrix[0,2]/gray.shape[1],matrix[1,2]/gray.shape[0]],np.float32)
        if np.max(np.abs(shift))>.06:return self.camera_shift.tolist()
        self.camera_shift=.25*shift+.75*self.camera_shift
        return self.camera_shift.tolist()

    def update(self,frame,index):
        if self.last_index is not None and index<=self.last_index:
            self.last=None; self.last_good=None; self.pending=None; self.pending_count=0
        self.last_index=index
        found=locate_strip(frame)
        camera_shift=self.estimate_camera_shift(frame)
        if found:found['camera_shift']=camera_shift
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
                found['camera_shift']=(.3*np.array(found['camera_shift'])+.7*np.array(self.last.get('camera_shift',[0,0]))).tolist()
            self.last=found
            self.last_good=index
            return dict(status='tracking',edges=copy.deepcopy(found))
        if self.last is not None and (index-self.last_good)/self.fps<=.5:
            return dict(status='held',edges=copy.deepcopy(self.last))
        return dict(status='lost',edges=None)
