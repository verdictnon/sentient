"""Verify the pinned partner upload and apply the reviewed integration overlay."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source, output, model_cache=None):
    source,output=Path(source).resolve(),Path(output).resolve()
    if output.exists():raise ValueError('Use a fresh build directory; existing application builds are never overwritten')
    upstream=json.loads((ROOT/'integration/upstream-p2-manifest.json').read_text())
    overlay=json.loads((ROOT/'integration/overlay-manifest.json').read_text())
    for name,info in upstream['files'].items():
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe source manifest path')
        path=source/relative
        if not path.is_file() or sha(path)!=info['sha256']:
            raise ValueError('Source differs from the inspected upload; merge explicitly: '+name)
    for name,info in overlay['files'].items():
        if sha(ROOT/'integration/app_overlay'/name)!=info['sha256']:
            raise ValueError('Overlay checksum mismatch: '+name)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent,prefix='.app-build-') as scratch:
        target=Path(scratch)/'app';target.mkdir()
        for name in list(upstream['files'])+['ARCHIVE_MANIFEST.json']:
            dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,dest)
        for name in overlay['files']:
            dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/'integration/app_overlay'/name,dest)
        if model_cache:
            cache=Path(model_cache).resolve()
            if not cache.is_dir():raise ValueError('Model cache does not exist')
            (target/'.local').mkdir();(target/'.local/p2').symlink_to(cache,target_is_directory=True)
        target.rename(output)
    return {'upstream_files_verified':len(upstream['files']),'overlay_files_verified':len(overlay['files']),'output':str(output)}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,default=ROOT/'Sentinel')
    p.add_argument('--output',type=Path,required=True);p.add_argument('--model-cache',type=Path)
    a=p.parse_args();print(json.dumps(build(a.source,a.output,a.model_cache),indent=2))
