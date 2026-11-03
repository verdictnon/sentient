"""Run the same P2 application job offline from a registered video or MP4 path."""

import argparse
import json
from pathlib import Path
import time

from app.p1.catalog import Catalog
from app.p1.media import register
from app.p2.jobs import JobManager

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'.local/workspace')
    parser.add_argument('--config',type=Path)
    parser.add_argument('--device',choices=['auto','cpu','cuda:0'],default='auto')
    parser.add_argument('--store',default='demo-store');parser.add_argument('--camera',default='camera-a')
    parser.add_argument('--session',default='session-a')
    args=parser.parse_args()
    catalog=Catalog(args.data_dir)
    video=register(catalog,args.input,store_id=args.store,camera_id=args.camera,stream_session_id=args.session)
    parameters=json.loads(args.config.read_text()) if args.config else {}
    parameters['device']=args.device
    manager=JobManager(catalog)
    try:
        job=manager.start(video['id'],parameters=parameters)
        while job['status'] in ('queued','running','cancelling'):
            time.sleep(.2);job=manager.get(job['id'])
        print(json.dumps(job,indent=2))
        return 0 if job['status']=='completed' else 2
    finally:manager.shutdown()


if __name__=='__main__':raise SystemExit(main())
