"""Download only explicitly selected, pinned inference assets outside Git."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.request
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_CACHE=ROOT/".local/p2"
SOURCE_URL="https://github.com/lyuwenyu/RT-DETR"
SOURCE_REVISION="df2a96c8c3a098567a1149bf0248da6efe6d495d"
WEIGHTS_NAME="rtdetrv2_r18vd_120e_coco_rerun_48.1.pth"
WEIGHTS_URL="https://github.com/lyuwenyu/storage/releases/download/v0.2/"+WEIGHTS_NAME
WEIGHTS_SHA256="2ace52184b620204004509b72752ac7bfe64aadaf7fc1d076b18df8ab5a5c77e"


R34_NAME="rtdetrv2_r34vd_120e_coco_ema.pth"
R34_URL="https://github.com/lyuwenyu/storage/releases/download/v0.1/"+R34_NAME
# Receiving audit fingerprint of the original author's release asset (not an upstream published digest).
R34_SHA256="ff617569a0b78adb857b3e7aefe10186e4222cdbf9661135ee0eb864c3fdb03e"


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(1024*1024):digest.update(chunk)
    return digest.hexdigest()


def download(url,path,expected=None):
    if path.is_file():
        if expected and sha256(path)!=expected:raise ValueError("Cached checkpoint SHA-256 mismatch; restore the pinned file")
        return
    temporary=path.with_suffix(path.suffix+".download")
    try:
        with urllib.request.urlopen(url,timeout=90) as response,temporary.open('wb') as output:
            while chunk:=response.read(1024*1024):output.write(chunk)
        if expected and sha256(temporary)!=expected:raise ValueError("Downloaded checkpoint SHA-256 does not match the pinned release")
        temporary.replace(path)
    finally:
        if temporary.exists():temporary.unlink()


def source_revision(path):
    # Read the cache's Git metadata directly. Spawning Git from a threaded
    # Windows server with inherited pipes can leave stdout waiting for EOF.
    # Fresh installations use the pinned ZIP marker and require no Git at all.
    path=Path(path)
    git=path/'.git'
    if git.is_dir():
        head=(git/'HEAD').read_text().strip()
        if head.startswith('ref: '):
            name=head[5:]
            reference=(git/name).resolve()
            if not reference.is_relative_to(git.resolve()):raise ValueError('Unsafe Git reference in detector cache')
            if reference.is_file():head=reference.read_text().strip()
            elif (git/'packed-refs').is_file():
                head=next((line.split(' ',1)[0] for line in (git/'packed-refs').read_text().splitlines()
                           if ' ' in line and line.split(' ',1)[1]==name),'')
        return head if re.fullmatch('[0-9a-f]{40}',head) else None
    marker=path/'.sentient-source-revision'
    return marker.read_text().strip() if marker.exists() else None


def ensure_source(cache):
    vendor=cache/'vendor';vendor.mkdir(parents=True,exist_ok=True)
    target=vendor/'RT-DETR'
    if target.exists():
        if source_revision(target)!=SOURCE_REVISION:raise ValueError("RT-DETR source revision differs from the pinned implementation")
        return target
    archive=vendor/'rtdetr-source.zip'
    download(f"https://codeload.github.com/lyuwenyu/RT-DETR/zip/{SOURCE_REVISION}",archive)
    with tempfile.TemporaryDirectory(prefix='source-',dir=vendor) as folder:
        scratch=Path(folder).resolve()
        assert scratch.is_relative_to(vendor.resolve())
        with ZipFile(archive) as zipped:
            for entry in zipped.infolist():
                if not (scratch/entry.filename).resolve().is_relative_to(scratch):raise ValueError("Unsafe source archive")
            zipped.extractall(scratch)
        extracted=scratch/f'RT-DETR-{SOURCE_REVISION}'
        assert extracted.resolve().is_relative_to(cache.resolve()) and target.resolve().is_relative_to(cache.resolve())
        extracted.rename(target)
    (target/'.sentient-source-revision').write_text(SOURCE_REVISION+'\n')
    return target


def prepare(cache=DEFAULT_CACHE,backend='rtdetrv2'):
    cache=Path(cache).resolve();cache.mkdir(parents=True,exist_ok=True)
    if backend in ('rtdetrv2','rtdetrv2_r34'):
        source=ensure_source(cache)
        name,url,expected=(WEIGHTS_NAME,WEIGHTS_URL,WEIGHTS_SHA256) if backend=='rtdetrv2' else (R34_NAME,R34_URL,R34_SHA256)
        path=cache/name
        download(url,path,expected)
        metadata={"implementation":"original RT-DETRv2-S / R18vd", "source_url":SOURCE_URL,
                  "source_revision":SOURCE_REVISION,"checkpoint_url":url,"checkpoint_file":name,
                  "checkpoint_sha256":expected,"license":"Apache-2.0 (upstream repository)",
                  "license_url":SOURCE_URL+'/blob/'+SOURCE_REVISION+'/LICENSE',
                  "configuration":"rtdetrv2_pytorch/configs/rtdetrv2/rtdetrv2_r18vd_120e_coco.yml",
                  "input_size":640,"person_class_index":0,"preprocessing":"RGB bilinear stretch resize; tensor /255; original-reference preprocessing"}
        if backend=='rtdetrv2_r34':
            metadata.update(implementation='original RT-DETRv2-M* / R34vd',
                configuration='rtdetrv2_pytorch/configs/rtdetrv2/rtdetrv2_r34vd_120e_coco.yml')
    elif backend=='torchvision_mobilenet':
        import torchvision
        from torchvision.models.detection import FasterRCNN_MobileNet_V3_Large_FPN_Weights
        url=FasterRCNN_MobileNet_V3_Large_FPN_Weights.COCO_V1.url
        path=cache/'torchvision_mobilenet_coco_v1.pth'
        download(url,path)
        metadata={"implementation":"torchvision fasterrcnn_mobilenet_v3_large_fpn COCO_V1",
                  "source_url":"https://github.com/pytorch/vision","source_revision":torchvision.__version__,
                  "checkpoint_url":url,"checkpoint_file":path.name,"checkpoint_sha256":sha256(path),
                  "license":"BSD-3-Clause (torchvision)","license_url":"https://github.com/pytorch/vision/blob/main/LICENSE",
                  "person_class_index":1,"preprocessing":"torchvision detector transform; RGB float /255; returned original coordinates"}
    else:raise ValueError("Unknown detector")
    (cache/f'{backend}.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    return metadata


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir',type=Path,default=DEFAULT_CACHE)
    parser.add_argument('--backend',choices=['rtdetrv2','rtdetrv2_r34','torchvision_mobilenet'],default='rtdetrv2')
    args=parser.parse_args()
    print(json.dumps(prepare(args.cache_dir,args.backend),indent=2))


if __name__=='__main__':main()
