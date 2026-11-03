"""Prune reviewed build/test/cache duplicates; preserve registered media and reviews."""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
from zipfile import ZipFile,ZIP_DEFLATED
ROOT=Path(__file__).resolve().parents[1]
TARGETS=[
 'runs/action_worker_v1/model_failed_swish','runs/action_worker_v1/rebuild_check',
 'runs/action_worker_v1/one_button_rebuild_check','runs/take_demo_v1/build_check',
 'runs/review_bridge_verification','runs/review_bridge_verification_final',
 'runs/review_bridge_verification_v2','runs/review_bridge_verification_v3',
 'runs/review_pack_smoke','runs/ui_playback_fix','runs/review_pack_v1','runs/review_pack_v2',
 'runs/action_worker_v1/scored_pack','runs/action_worker_v1/scored_pack_final',
 'runs/action_worker_v1/review_pack','runs/action_worker_v1/original_workspace_backup',
 'runs/action_worker_v1/workspace','runs/action_worker_v1/release_roundtrip','runs/action_worker_v1/roundtrip_final',
 'runs/action_worker_v1/continuous_export_verification','runs/action_worker_v1/continuous_filtered',
 'runs/action_worker_v1/continuous_filtered_final','runs/action_worker_v1/continuous_filtered_startup_failure',
 'runs/action_worker_v1/continuous_runner_stage_failure',
 'runs/x3d_s_v2/early.npy','data/public_retail_hd/carr/range_cache',
 'runs/action_worker_v1/app',
]
ARCHIVE=['Sentinel','Sentient_P2_Validation_Review','integration']

def size(p):
    if p.is_symlink():return 0
    if p.is_file():return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob('*') if f.is_file() and not f.is_symlink())

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    db=sqlite3.connect(ROOT/'runs/review_workspace/catalog.sqlite');media=[Path(r[0]).resolve() for r in db.execute('SELECT path FROM assets')];db.close()
    receipt=[]
    for name in ARCHIVE:
        p=ROOT/name
        if not p.exists():continue
        if args.apply:
            dest=ROOT/'docs/archive'/(name+'.zip')
            with ZipFile(dest,'w',ZIP_DEFLATED) as z:
                for f in p.rglob('*'):
                    if f.is_file() and not f.is_symlink() and '__pycache__' not in f.parts and '.local' not in f.parts:z.write(f,f.relative_to(ROOT))
        TARGETS.append(name)
    for name in TARGETS:
        p=ROOT/name
        if not p.exists() and not p.is_symlink():continue
        # Registered assets in the active catalog are never removed.
        if any(m==p.resolve() or m.is_relative_to(p.resolve()) for m in media):
            receipt.append({'path':name,'action':'kept','reason':'active registered media dependency'});continue
        if p.is_dir():
            has_reviews=False
            for catalog in p.rglob('catalog.sqlite'):
                d=sqlite3.connect(catalog)
                try:has_reviews=has_reviews or d.execute('SELECT COUNT(*) FROM annotations').fetchone()[0]>0
                except sqlite3.OperationalError:pass
                finally:d.close()
            if has_reviews:
                receipt.append({'path':name,'action':'kept','reason':'contains human review history'});continue
        n=size(p);receipt.append({'path':name,'action':'deleted' if args.apply else 'would_delete','bytes':n})
        if args.apply:
            if p.is_symlink() or p.is_file():p.unlink()
            else:shutil.rmtree(p)
    if args.apply:
        # Retired overlay builder is retained with its input archive, not offered as
        # the current application build route.
        p=ROOT/'scripts/build_integrated_app.py'
        if p.exists():shutil.move(p,ROOT/'docs/archive/build_integrated_app.py')
    report={'apply':args.apply,'freed_bytes':sum(x.get('bytes',0) for x in receipt),'entries':receipt,
            'kept':'working model, source checkpoint, pooled training features, both environments, source datasets, active catalog/media and review history'}
    (ROOT/'runs/demo_live_v2/cleanup.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
