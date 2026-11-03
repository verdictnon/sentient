"""Export/run the real local action model. Outputs are review suggestions only."""
import argparse
import json
import os
from pathlib import Path

# Required by deterministic CUDA matmul; set before Torch creates a CUDA context.
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
from worker.model import ROOT, Runtime, export_model
from worker.pipeline import Policy, run_video


def configure_torch():
    import torch
    torch.use_deterministic_algorithms(True)
    torch.backends.cuda.matmul.allow_tf32=False
    torch.backends.cudnn.allow_tf32=False
    torch.backends.cudnn.benchmark=False


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    export=sub.add_parser('export',help='One-time trusted local checkpoint -> portable TorchScript artifact')
    export.add_argument('--checkpoint',type=Path,default=ROOT/'runs/x3d_s_v5/selected_full.pt')
    export.add_argument('--output',type=Path,required=True)
    run=sub.add_parser('run',help='MP4 + original-coordinate v1 tracks -> candidates, scores and evidence')
    run.add_argument('--video',type=Path,required=True)
    run.add_argument('--tracks',type=Path,required=True)
    run.add_argument('--model',type=Path,required=True)
    run.add_argument('--output',type=Path,required=True)
    run.add_argument('--source-kind',choices=['continuous','sparse_clip'],required=True)
    run.add_argument('--device',choices=['auto','cpu','cuda'],default='auto')
    run.add_argument('--window-ms',type=float,default=2600)
    run.add_argument('--stride-ms',type=float,default=1300)
    run.add_argument('--window-frames',type=int,default=13)
    run.add_argument('--stride-frames',type=int,default=6)
    run.add_argument('--max-gap-ms',type=float,default=1500)
    run.add_argument('--batch-size',type=int,default=2)
    run.add_argument('--max-candidates',type=int,default=10000)
    run.add_argument('--max-active-windows',type=int,default=64)
    pack=sub.add_parser('pack',help='Score the existing public-source review queue and build a bridge-compatible pack')
    pack.add_argument('--base-pack',type=Path,required=True,help='Explicit review pack manifest; generate it with review_bridge.py prepare')
    pack.add_argument('--model',type=Path,required=True)
    pack.add_argument('--output',type=Path,required=True)
    pack.add_argument('--device',choices=['auto','cpu','cuda'],default='auto')
    pack.add_argument('--limit-groups',type=int)
    args=p.parse_args()
    configure_torch()
    if args.command=='export':
        result=export_model(args.checkpoint,args.output)
    elif args.command=='pack':
        from worker.pack import build_pack
        result=build_pack(args.base_pack,args.model,args.output,args.device,args.limit_groups)
    else:
        policy=Policy(source_kind=args.source_kind,window_ms=args.window_ms,stride_ms=args.stride_ms,
            window_frames=args.window_frames,stride_frames=args.stride_frames,max_gap_ms=args.max_gap_ms,
            batch_size=args.batch_size,max_candidates=args.max_candidates,max_active_windows=args.max_active_windows)
        runtime=Runtime(args.model,args.device)
        result=run_video(args.video,args.tracks,args.model,args.output,policy,runtime)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
