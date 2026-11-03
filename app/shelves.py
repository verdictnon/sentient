"""Observed footpoint visits and dwell in named image-space areas."""
from app.geometry import Polygon, Line


class ShelfStats:
    def __init__(self, geometry, physical_time=True):
        self.physical_time = physical_time
        self.areas = {s['id']: s for s in geometry['shapes'] if s['kind'] in ('shelf', 'zone', 'checkout', 'exit', 'queue')}
        self.polygons = {k: Polygon(tuple(map(tuple, s['points']))) for k,s in self.areas.items()}
        self.lines = {s['id']: (s, Line(*map(tuple, s['points']), s.get('hysteresis_px',5))) for s in geometry['shapes'] if s['kind']=='line'}
        self.stats = {k: dict(id=k,name=s['name'],kind=s['kind'],visits=0,observed_dwell_seconds=0.,pickup_alerts=0) for k,s in self.areas.items()}
        self.visitors = {k:set() for k in self.areas}
        self.last = {}; self.occupants={k:set() for k in self.areas}; self.pickup_ids=set()
        self.crossings={k:dict(id=k,name=s['name'],positive=0,negative=0) for k,(s,_) in self.lines.items()}
        self.line_sides={}

    def update(self, time_ms, tracks, known_inside=None):
        current={k:set() for k in self.areas}
        for t in tracks:
            if t.get('position_status')!='observed': continue
            identity=t['local_track_id']; x1,y1,x2,y2=t['box']; point=((x1+x2)/2,y2)
            inside=known_inside[identity] if known_inside is not None else {k for k,p in self.polygons.items() if p.contains(point)}
            previous=self.last.get(identity)
            contiguous=previous is not None and 0 < time_ms-previous[0] <= 1000
            for k in inside:
                current[k].add(identity);self.visitors[k].add(identity)
                if not contiguous or k not in previous[1]: self.stats[k]['visits']+=1
                elif self.physical_time: self.stats[k]['observed_dwell_seconds']+=(time_ms-previous[0])/1000
            for k,(_,line) in self.lines.items():
                side=line.side(point); old=self.line_sides.get((k,identity))
                if side:
                    if self.physical_time and old and 0<time_ms-old[0]<=1000 and side!=old[1] and line.crossed_segment(old[2],point):
                        self.crossings[k]['positive' if side>0 else 'negative']+=1
                    self.line_sides[k,identity]=(time_ms,side,point)
            self.last[identity]=(time_ms,inside)
        self.occupants=current
        # Retain only recent states. Unique track sets are exact for this session.
        self.last={k:v for k,v in self.last.items() if time_ms-v[0]<=1500}
        self.line_sides={k:v for k,v in self.line_sides.items() if time_ms-v[0]<=1500}

    def pickup(self,event,track):
        key=event['id']
        if key in self.pickup_ids:return
        self.pickup_ids.add(key)
        x1,y1,x2,y2=track['box'];point=((x1+x2)/2,y2)
        event['shelf_ids']=[k for k,p in self.polygons.items() if self.areas[k]['kind']=='shelf' and p.contains(point)]
        for k in event['shelf_ids']:self.stats[k]['pickup_alerts']+=1

    def snapshot(self):
        rows=[s|{'unique_tracks':len(self.visitors[k]),'occupancy':len(self.occupants[k]),
                 'observed_dwell_seconds':round(s['observed_dwell_seconds'],2) if self.physical_time else None} for k,s in self.stats.items()]
        return {'areas':sorted(rows,key=lambda r:(-r['unique_tracks'],-(r['observed_dwell_seconds'] or 0),r['name'])),
                'crossings':list(self.crossings.values()) if self.physical_time else [],
                'physical_time_supported':self.physical_time,'identity_scope':'camera-local tracks',
                'method':'Observed box bottom-centre in floor polygon; dwell only between consecutive observations <=1s; pickup alerts are model predictions, not sales.'}


def router(manager):
    import json
    from fastapi import APIRouter
    from app.p1.geometry import load_geometry
    from app.p2.persistence import read_database
    r=APIRouter(prefix='/api/shelves')
    @r.get('/jobs/{job_id}')
    def report(job_id:str):
        job=manager.get(job_id)
        if job['status']!='completed':raise ValueError('Wait for tracking to finish')
        saved=load_geometry(manager.catalog,job['video_id'])
        stats=ShelfStats(saved['geometry'],job['config'].get('source_kind')!='sparse_clip')
        db=read_database(job['database_path'])
        try:
            last_time=None;batch=[]
            for row in db.execute("SELECT payload FROM inputs WHERE kind='observation' ORDER BY media_time_ms,track"):
                t=json.loads(row[0]);ms=t['media_time_ms']
                if last_time is not None and ms!=last_time:stats.update(last_time,batch);batch=[]
                batch.append({'local_track_id':t['local_track_id'],'box':t['bbox_xyxy_px'],'position_status':t['position_status']});last_time=ms
            if batch:stats.update(last_time,batch)
        finally:db.close()
        # Associate alerts with the observed footpoint at detection/window end.
        with manager.catalog.connect() as catalog_db:
            sets=catalog_db.execute('SELECT p.payload FROM pickup_event_sets p JOIN action_analysis_jobs j ON json_extract(j.payload,\'$.analysis_id\')=p.analysis_id WHERE json_extract(j.payload,\'$.tracking_job_id\')=? ORDER BY j.rowid DESC LIMIT 1',(job_id,)).fetchone()
        if sets:
            db=read_database(job['database_path'])
            try:
                for event in json.loads(sets[0]).get('events',[]):
                    row=db.execute("SELECT payload FROM inputs WHERE kind='observation' AND json_extract(payload,'$.local_track_id')=? AND json_extract(payload,'$.position_status')='observed' AND media_time_ms BETWEEN ? AND ? ORDER BY media_time_ms DESC LIMIT 1",(event['local_track_id'],event['end_media_ms']-1000,event['end_media_ms'])).fetchone()
                    if row:
                        t=json.loads(row[0]);stats.pickup({'id':event.get('id',event.get('event_id',str(event)))},{'box':t['bbox_xyxy_px']})
            finally:db.close()
        return stats.snapshot()|{'geometry_id':saved['id'],'tracking_job_id':job_id,'occupancy_basis':'last observed source sample, not current wall time'}
    return r
