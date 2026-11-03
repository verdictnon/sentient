"""Additive P2 routes; P1 import/review/export APIs remain unchanged."""

import json
import math
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.p2.persistence import read_database
from app.geometry import Polygon


class StartRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    geometry_id: str | None = None
    config: dict = Field(default_factory=dict)


def router(manager):
    routes=APIRouter(prefix='/api/tracking')

    @routes.get('/model')
    def model():
        path=manager.model_dir/'rtdetrv2.json'
        if not path.is_file():return {'files_present':False,'setup_command':'python -m app.p2.setup'}
        metadata=json.loads(path.read_text())
        return {'files_present':(manager.model_dir/metadata['checkpoint_file']).is_file(),'metadata':metadata}

    @routes.post('/videos/{video_id}')
    def start(video_id:str,body:StartRequest):
        return manager.start(video_id,body.geometry_id,body.config)

    @routes.get('/videos/{video_id}')
    def jobs(video_id:str):
        manager.catalog.video(video_id)
        return manager.list(video_id)

    @routes.get('/jobs/{job_id}')
    def job(job_id:str):return manager.get(job_id)

    @routes.post('/jobs/{job_id}/cancel')
    def cancel(job_id:str):return manager.cancel(job_id)

    @routes.post('/jobs/{job_id}/replay')
    def replay(job_id:str):return manager.replay(job_id)

    @routes.get('/jobs/{job_id}/output')
    def output(job_id:str):
        job=manager.get(job_id)
        if job['status']!='completed':raise ValueError('Tracking output is incomplete')
        return FileResponse(job['output_path'],media_type='application/x-ndjson',filename='sentient-person-tracks.jsonl')

    @routes.get('/jobs/{job_id}/observations')
    def observations(job_id:str,frame_index:int|None=Query(None,ge=0),media_ms:float|None=Query(None,allow_inf_nan=False)):
        job=manager.get(job_id)
        if job['status'] not in ('running','completed'):
            return {'frame_index':frame_index,'observations':[],'reason':'Tracking output is not complete/active'}
        video=manager.catalog.video(job['video_id'])
        if frame_index is None:
            if media_ms is None:raise ValueError('Specify an exact frame index or presented media timestamp')
            # requestVideoFrameCallback can round a rational timestamp. Never
            # substitute boxes from a different nearby frame across a time gap.
            with manager.catalog.connect() as db:
                row=db.execute('SELECT frame_index FROM frames WHERE sha=? AND media_ms BETWEEN ? AND ? '
                               'ORDER BY ABS(media_ms-?) LIMIT 1',
                               (video['media_sha256'],media_ms-.01,media_ms+.01,media_ms)).fetchone()
            if row is None:return {'frame_index':None,'observations':[],'reason':'No indexed frame at the presented source PTS'}
            frame_index=row[0]
        reference=manager.catalog.frame(video,frame_index)
        db=read_database(job['database_path'])
        try:
            rows=db.execute("SELECT payload FROM inputs WHERE kind='observation' AND json_extract(payload,'$.frame_index')=? ORDER BY track",(frame_index,))
            records=[json.loads(row[0]) for row in rows]
            quality=db.execute('SELECT detector_ran FROM p2_frame_info WHERE frame_index=?',(frame_index,)).fetchone()
        finally:db.close()
        detector_ran=bool(quality[0]) if quality is not None else None
        zone=Polygon(tuple(tuple(p) for p in job['p0_config']['zone']))
        in_zone=[r for r in records if zone.contains(((r['bbox_xyxy_px'][0]+r['bbox_xyxy_px'][2])/2,r['bbox_xyxy_px'][3]))]
        return {'frame_index':frame_index,'media_time_ms':reference['media_ms'],'source_pts':reference['pts'],
                'time_base':reference['time_base'],'observations':records,'detector_ran':detector_ran,
                'zone_observed_count':sum(r['position_status']=='observed' for r in in_zone) if detector_ran else None,
                'zone_predicted_count':sum(r['position_status']=='predicted' for r in in_zone)}

    @routes.get('/jobs/{job_id}/tracks')
    def tracks(job_id:str,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500)):
        job=manager.get(job_id)
        db=read_database(job['database_path'])
        try:
            rows=db.execute("SELECT track AS local_track_id,MIN(media_time_ms) AS first_media_ms,MAX(media_time_ms) AS last_media_ms,"
                            "COUNT(*) AS samples,SUM(json_extract(payload,'$.position_status')='observed') AS observed_samples "
                            "FROM inputs WHERE kind='observation' GROUP BY track ORDER BY track LIMIT ? OFFSET ?",(limit,offset))
            return [dict(row) for row in rows]
        finally:db.close()

    @routes.get('/jobs/{job_id}/paths')
    def paths(job_id:str,max_points:int=Query(300,ge=2,le=2000)):
        job=manager.get(job_id);db=read_database(job['database_path'])
        grouped={}
        try:
            for row in db.execute("SELECT payload FROM inputs WHERE kind='observation' ORDER BY media_time_ms,track"):
                record=json.loads(row[0])
                if record['position_status']!='observed':continue
                x1,y1,x2,y2=record['bbox_xyxy_px']
                grouped.setdefault(record['local_track_id'],[]).append([record['frame_index'],record['media_time_ms'],(x1+x2)/2,y2])
        finally:db.close()
        result=[]
        for identity,points in grouped.items():
            segments=[];current=[]
            for point in points:
                if current and job['config'].get('source_kind')!='sparse_clip' and point[1]-current[-1][1]>1500:
                    segments.append(current);current=[]
                current.append(point)
            if current:segments.append(current)
            sampled=[]
            for segment in segments:
                if len(segment)>max_points:
                    ids=sorted({round(i*(len(segment)-1)/(max_points-1)) for i in range(max_points)})
                    segment=[segment[i] for i in ids]
                sampled.append(segment)
            result.append({'local_track_id':identity,'segments':sampled})
        return {'job_id':job_id,'coordinates':'original_image_pixels','positions':'observed_only','tracks':result}

    @routes.get('/jobs/{job_id}/tracks/{track_id}')
    def trajectory(job_id:str,track_id:str,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500)):
        job=manager.get(job_id);db=read_database(job['database_path'])
        try:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM inputs WHERE kind='observation' AND track=? "
                                                           "ORDER BY media_time_ms LIMIT ? OFFSET ?",(track_id,limit,offset))]
        finally:db.close()

    return routes
