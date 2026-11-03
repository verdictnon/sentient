"""App-owned ByteTrack-style two-stage association and time-aware Kalman motion.

No appearance embeddings, global identity, service inference or frame-rate
assumptions. This is a detector-independent compatible algorithm, not a claim
of bit-identical output from the original ByteTrack implementation.
"""

from dataclasses import dataclass
import math
import numpy as np
from scipy.optimize import linear_sum_assignment

from app.p2.config import TrackingConfig

TRACKER_VERSION = "sentient-bytetrack/1"


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]
    score: float
    class_id: int = 0
    frame_index: int | None = None

    def __post_init__(self):
        if len(self.box) != 4 or not all(math.isfinite(v) for v in self.box):
            raise ValueError("Detection boxes require four finite xyxy coordinates")
        x1,y1,x2,y2=self.box
        if x1 >= x2 or y1 >= y2 or not math.isfinite(self.score) or not 0 <= self.score <= 1:
            raise ValueError("Invalid detection area or confidence")


def clip_box(box, dimensions):
    w,h=dimensions
    x1,y1,x2,y2=map(float,box)
    result=(max(0.,min(w,x1)),max(0.,min(h,y1)),max(0.,min(w,x2)),max(0.,min(h,y2)))
    return result if result[0] < result[2] and result[1] < result[3] else None


def iou(a,b):
    width=max(0,min(a[2],b[2])-max(a[0],b[0]))
    height=max(0,min(a[3],b[3])-max(a[1],b[1]))
    intersection=width*height
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection/union if union>0 else 0.


class _Track:
    def __init__(self,number,detection,time_ms):
        x1,y1,x2,y2=detection.box
        self.number=number
        self.mean=np.array([(x1+x2)/2,(y1+y2)/2,x2-x1,y2-y1,0,0,0,0],dtype=float)
        self.cov=np.diag([10.,10.,10.,10.,100.,100.,100.,100.])
        self.last_observed=time_ms
        self.score=detection.score
        self.measured=detection.box

    def predict(self,dt):
        transition=np.eye(8)
        transition[:4,4:]=np.eye(4)*dt
        scale=max(1.,self.mean[3]*.05)
        noise=np.diag([scale**2*max(dt,.001)]*4+[scale**2*max(dt,.001)]*4)
        self.mean=transition@self.mean
        self.cov=transition@self.cov@transition.T+noise
        self.mean[2:4]=np.maximum(1.,self.mean[2:4])

    def update(self,detection,time_ms):
        x1,y1,x2,y2=detection.box
        measurement=np.array([(x1+x2)/2,(y1+y2)/2,x2-x1,y2-y1])
        observation=np.eye(4,8)
        variance=np.eye(4)*max(1.,self.mean[3]*.025)**2
        innovation=observation@self.cov@observation.T+variance
        gain=np.linalg.solve(innovation,(self.cov@observation.T).T).T
        self.mean+=gain@(measurement-observation@self.mean)
        identity=np.eye(8)-gain@observation
        self.cov=identity@self.cov@identity.T+gain@variance@gain.T
        self.last_observed=time_ms
        self.score=detection.score
        self.measured=detection.box

    def box(self):
        x,y,w,h=self.mean[:4]
        return (x-w/2,y-h/2,x+w/2,y+h/2)


class ByteTracker:
    def __init__(self,config: TrackingConfig):
        self.config=config
        self.tracks=[]
        self.next_id=1
        self.last_time=None
        self.capacity_drops=0

    @staticmethod
    def _associate(tracks,detections,threshold):
        if not tracks or not detections:
            return [],list(range(len(tracks))),list(range(len(detections)))
        overlap=np.array([[iou(t.box(),d.box) for d in detections] for t in tracks])
        # Impossible edges must not steal a viable edge during global assignment.
        costs=np.where(overlap>=threshold,1-overlap,1e6)
        rows,cols=linear_sum_assignment(costs)
        matches=[(int(r),int(c)) for r,c in zip(rows,cols) if overlap[r,c]>=threshold]
        used_t={r for r,_ in matches};used_d={c for _,c in matches}
        return matches,[r for r in range(len(tracks)) if r not in used_t],[c for c in range(len(detections)) if c not in used_d]

    def advance(self,detections,time_ms,dimensions):
        if not math.isfinite(time_ms) or (self.last_time is not None and time_ms<=self.last_time):
            raise ValueError("Tracker source timestamps must strictly increase")
        dt=0 if self.last_time is None else (time_ms-self.last_time)/1000
        self.last_time=time_ms
        self.tracks=[t for t in self.tracks if time_ms-t.last_observed<=self.config.max_lost_ms]
        for track in self.tracks:track.predict(dt)
        observed=set()
        if detections is not None:
            people=sorted((d for d in detections if d.class_id==0 and d.score>=self.config.low_threshold),
                          key=lambda d:(-d.score,*d.box))
            high=[d for d in people if d.score>=self.config.high_threshold]
            low=[d for d in people if d.score<self.config.high_threshold]
            matches,unmatched,_=self._associate(self.tracks,high,self.config.match_iou)
            used_high=set()
            for row,col in matches:
                track=self.tracks[row];track.update(high[col],time_ms)
                observed.add(track.number);used_high.add(col)
            remaining=[self.tracks[i] for i in unmatched]
            matches_low,_,_=self._associate(remaining,low,self.config.second_match_iou)
            for row,col in matches_low:
                track=remaining[row];track.update(low[col],time_ms);observed.add(track.number)
            for index,detection in enumerate(high):
                if index in used_high:continue
                if len(self.tracks)>=self.config.max_tracks:
                    self.capacity_drops+=1;continue
                track=_Track(self.next_id,detection,time_ms)
                self.next_id+=1;self.tracks.append(track);observed.add(track.number)
        result=[]
        for track in sorted(self.tracks,key=lambda t:t.number):
            status="observed" if track.number in observed else "predicted"
            box=clip_box(track.measured if status=="observed" else track.box(),dimensions)
            if box is not None:
                result.append({"local_track_id":str(track.number),"box":box,"score":track.score,"position_status":status})
        return result
