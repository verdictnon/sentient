"""Verified pretrained adapters. Nothing is downloaded or simulated during a job."""

import gc
import importlib
import json
import os
from pathlib import Path
import sys

# CUDA deterministic matmul needs this before the first Torch/CUDA context.
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import numpy as np
from PIL import Image

from app.p2.setup import DEFAULT_CACHE, SOURCE_REVISION, WEIGHTS_SHA256, R34_SHA256, sha256, source_revision
from app.p2.tracker import Detection, clip_box


def remap_box(box,original,network=(640,640),*,padding=(0,0),scale=None):
    """Undo exact resize/letterbox parameters once, then clip original pixels."""
    w,h=original;nw,nh=network
    sx,sy=(scale,scale) if scale is not None else (nw/w,nh/h)
    px,py=padding
    return clip_box(((box[0]-px)/sx,(box[1]-py)/sy,(box[2]-px)/sx,(box[3]-py)/sy),original)


def convert_outputs(boxes,scores,labels,dimensions,frame_index,threshold,*,person_class=0,network=None):
    detections=[]
    for box,score,label in zip(boxes,scores,labels):
        if int(label)!=person_class or float(score)<threshold:continue
        original=remap_box(box,dimensions,network) if network else clip_box(box,dimensions)
        if original is not None:
            detections.append(Detection(original,float(score),0,frame_index))
    return sorted(detections,key=lambda d:(-d.score,*d.box))


def filter_people(detections,dimensions,min_area_ratio=0.,nms_iou=0.):
    """Optional scene settings, disabled by default; never create observations."""
    width,height=dimensions
    ordered=sorted(detections,key=lambda d:(-d.score,*d.box))
    kept=[]
    for detection in ordered:
        a,b,c,d=detection.box;area=(c-a)*(d-b)
        if area/(width*height)<min_area_ratio:continue
        duplicate=False
        if nms_iou:
            for other in kept:
                e,f,g,h=other.box
                intersection=max(0,min(c,g)-max(a,e))*max(0,min(d,h)-max(b,f))
                union=area+(g-e)*(h-f)-intersection
                if union and intersection/union>nms_iou:
                    duplicate=True;break
        if not duplicate:kept.append(detection)
    return kept


def model_metadata(cache,backend):
    path=Path(cache)/f'{backend}.json'
    if not path.is_file():raise FileNotFoundError("Model not installed. Run: python -m app.p2.setup --backend "+backend)
    metadata=json.loads(path.read_text(encoding='utf-8'))
    checkpoint=Path(cache)/metadata['checkpoint_file']
    if not checkpoint.is_file():raise FileNotFoundError("Pretrained checkpoint is missing; run python -m app.p2.setup")
    actual=sha256(checkpoint)
    expected=WEIGHTS_SHA256 if backend=='rtdetrv2' else R34_SHA256 if backend=='rtdetrv2_r34' else metadata['checkpoint_sha256']
    if actual!=expected or metadata['checkpoint_sha256']!=expected:raise ValueError("Checkpoint SHA-256 verification failed")
    if backend in ('rtdetrv2','rtdetrv2_r34') and source_revision(Path(cache)/'vendor/RT-DETR')!=SOURCE_REVISION:
        raise ValueError("Detector implementation source does not match pinned RT-DETR revision")
    return metadata


class PretrainedDetector:
    def __init__(self,config,cache=DEFAULT_CACHE):
        self.metadata=model_metadata(cache,config.detector)
        import torch
        import torchvision
        torch.manual_seed(config.seed)
        np.random.seed(config.seed)
        torch.set_num_threads(2)
        torch.backends.cuda.matmul.allow_tf32=False
        torch.backends.cudnn.allow_tf32=False
        torch.backends.cudnn.benchmark=False
        torch.backends.cudnn.deterministic=True
        torch.use_deterministic_algorithms(True)
        self.device='cuda:0' if config.device=='auto' and torch.cuda.is_available() else ('cpu' if config.device=='auto' else config.device)
        if self.device.startswith('cuda') and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is unavailable in this PyTorch installation; select CPU or auto")
        self.config=config
        self.torch=torch
        checkpoint=Path(cache)/self.metadata['checkpoint_file']
        if config.detector in ('rtdetrv2','rtdetrv2_r34'):
            source=Path(cache)/'vendor/RT-DETR/rtdetrv2_pytorch'
            existing=sys.modules.get('src')
            if existing is not None and not Path(existing.__file__).resolve().is_relative_to(source.resolve()):
                raise RuntimeError("RT-DETR's src namespace conflicts with an already imported package")
            if str(source.resolve()) not in sys.path:sys.path.insert(0,str(source.resolve()))
            YAMLConfig=importlib.import_module('src.core').YAMLConfig
            cfg=YAMLConfig(str(Path(cache)/'vendor/RT-DETR'/self.metadata['configuration']))
            cfg.yaml_cfg['PResNet']['pretrained']=False
            cfg.yaml_cfg['remap_mscoco_category']=False
            state=torch.load(checkpoint,map_location='cpu',weights_only=True)
            weights=state['ema']['module'] if 'ema' in state else state.get('model',state)
            model=cfg.model
            model.load_state_dict(weights,strict=True)
            self.model=model.deploy().eval().to(self.device)
            self.post=cfg.postprocessor.deploy().eval().to(self.device)
            del state,weights,cfg,model
        else:
            from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_fpn
            self.model=fasterrcnn_mobilenet_v3_large_fpn(weights=None,weights_backbone=None)
            self.model.load_state_dict(torch.load(checkpoint,map_location='cpu',weights_only=True),strict=True)
            self.model.eval().to(self.device)
            self.post=None
        if self.device.startswith('cuda'):torch.cuda.reset_peak_memory_stats()
        self.metadata=self.metadata|{'torch_version':torch.__version__,'torchvision_version':torchvision.__version__,
                                     'device':self.device,'device_name':torch.cuda.get_device_name() if self.device.startswith('cuda') else 'CPU',
                                     'seed':config.seed,'deterministic_algorithms':True}
        gc.collect()

    def detect(self,image: Image.Image,frame_index: int):
        torch=self.torch
        image=image.convert('RGB');dimensions=image.size
        with torch.inference_mode():
            if self.config.detector in ('rtdetrv2','rtdetrv2_r34'):
                resized=image.resize((640,640),Image.Resampling.BILINEAR)
                tensor=torch.from_numpy(np.asarray(resized).copy()).permute(2,0,1).float().div_(255).unsqueeze(0).to(self.device)
                sizes=torch.tensor([[640,640]],device=self.device)
                labels,boxes,scores=self.post(self.model(tensor),sizes)
                result=convert_outputs(boxes[0].cpu().tolist(),scores[0].cpu().tolist(),labels[0].cpu().tolist(),
                                       dimensions,frame_index,self.config.low_threshold,network=(640,640))
            else:
                tensor=torch.from_numpy(np.asarray(image).copy()).permute(2,0,1).float().div_(255).to(self.device)
                out=self.model([tensor])[0]
                result=convert_outputs(out['boxes'].cpu().tolist(),out['scores'].cpu().tolist(),out['labels'].cpu().tolist(),
                                       dimensions,frame_index,self.config.low_threshold,person_class=1)
        return filter_people(result,dimensions,self.config.min_person_area_ratio,self.config.person_nms_iou)

    def memory(self):
        if not self.device.startswith('cuda'):return {'gpu_peak_allocated_bytes':None,'gpu_peak_reserved_bytes':None}
        return {'gpu_peak_allocated_bytes':self.torch.cuda.max_memory_allocated(),
                'gpu_peak_reserved_bytes':self.torch.cuda.max_memory_reserved()}

    def close(self):
        self.model=None;self.post=None
        gc.collect()
        if self.device.startswith('cuda'):self.torch.cuda.empty_cache()
