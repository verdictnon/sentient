"""Explicit engineering fixtures. Never a production detector or accuracy claim."""

import time
from app.p2.tracker import Detection


class FixtureDetector:
    def __init__(self,mode='moving',delay=0,signature='e'):
        self.mode=mode;self.delay=delay;self.closed=False
        self.metadata={'implementation':'ENGINEERING FIXTURE, NOT REAL PERSON DETECTION',
                       'checkpoint_sha256':signature*64,'source_revision':'fixture/1','engineering_test':True,'device':'cpu'}
    def detect(self,image,frame_index):
        if self.delay:time.sleep(self.delay)
        if self.mode=='error' and frame_index>=2:raise RuntimeError('Deliberate test inference failure')
        if self.mode=='empty':return []
        width,height=image.size
        x=(10+frame_index*2)*width/100
        return [Detection((x-width*.1,height*.2,x+width*.1,height*.8),.9,0,frame_index)]
    def memory(self):return {'gpu_peak_allocated_bytes':None,'gpu_peak_reserved_bytes':None}
    def close(self):
        self.closed=True
        if self.mode=='cleanup_error':raise RuntimeError('Deliberate model cleanup failure')


class FixtureFactory:
    def __init__(self,mode='moving',delay=0,signature='e'):
        self.mode=mode;self.delay=delay;self.signature=signature;self.created=[]
    def provenance(self,config):return FixtureDetector(signature=self.signature).metadata
    def __call__(self,config):
        detector=FixtureDetector(self.mode,self.delay,self.signature)
        self.created.append(detector)
        return detector


def wait_job(manager,job_id,timeout=30):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        job=manager.get(job_id)
        if job['status'] in ('completed','error','cancelled') and job.get('metrics') is not None:return job
        time.sleep(.02)
    raise AssertionError('Tracking job timed out: '+str(manager.get(job_id)))
