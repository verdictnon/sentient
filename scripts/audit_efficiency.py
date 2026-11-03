"""Bounded hardware/input audit; never fits or overwrites experiment checkpoints.

Run with .venv/bin/python scripts/audit_efficiency.py. Synthetic gradient steps
measure capacity only. Real-input timings use source-training views only.
"""
import gc
import json
import platform
import statistics
import subprocess
import time

import torch

from train_pilot import ROOT
from pytorchvideo.models.hub.x3d import x3d_s


def training_probe(frames, size, batch):
    model = None
    optimizer = None
    x = None
    loss = None
    result = {"frames": frames, "size": size, "batch": batch}
    try:
        torch.manual_seed(20261102)
        pretrained = x3d_s(pretrained=False)
        pretrained.load_state_dict(torch.load(
            ROOT / "models/X3D_S.pyth", weights_only=True, map_location="cpu"
        )["model_state"])
        # Variable-duration/resolution probe; not the historical fixed X3D head.
        model = torch.nn.Sequential(
            *list(pretrained.blocks)[:5], torch.nn.AdaptiveAvgPool3d(1),
            torch.nn.Flatten(), torch.nn.Linear(192, 3)
        ).cuda().train()
        del pretrained
        for layer in model.modules():
            if isinstance(layer, torch.nn.modules.batchnorm._BatchNorm):
                layer.eval()
                layer.requires_grad_(False)
        optimizer = torch.optim.AdamW(
            [p for p in model.parameters() if p.requires_grad], lr=1e-5
        )
        scaler = torch.amp.GradScaler("cuda")
        x = torch.randn(batch, 3, frames, size, size, device="cuda")
        target = torch.zeros(batch, 3, device="cuda")
        torch.cuda.reset_peak_memory_stats()
        elapsed = []
        for step in range(7):
            torch.cuda.synchronize()
            start = time.perf_counter()
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=torch.float16):
                loss = torch.nn.functional.binary_cross_entropy_with_logits(model(x), target)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            torch.cuda.synchronize()
            if step >= 2:
                elapsed.append((time.perf_counter() - start) * 1000)
        result.update(
            fits=True, median_step_ms=statistics.median(elapsed),
            peak_allocated_mib=torch.cuda.max_memory_allocated() / 2**20,
            peak_reserved_mib=torch.cuda.max_memory_reserved() / 2**20,
            trainable_parameters=sum(p.numel() for p in model.parameters() if p.requires_grad),
        )
    except torch.cuda.OutOfMemoryError:
        result.update(fits=False, reason="CUDA out of memory under 70% allocator cap")
    finally:
        model = optimizer = x = loss = None
        gc.collect()
        torch.cuda.empty_cache()
    return result


def real_input_probe():
    # Read completed index directly; do not call plan() or mutate its artifacts.
    from features_v6 import prepare, encoder
    from features_v3 import prepare as context_prepare
    info = json.loads((ROOT / "runs/hand_detail_v6_scale512/index.json").read_text())
    chosen = []
    seen = set()
    for row in info["views"]:
        key = row["split"], row["sample_id"]
        if row["pilot_role"] == "train" and key not in seen:
            chosen.append(row)
            seen.add(key)
        if len(chosen) == 8:
            break
    model = encoder().cuda()
    mean = torch.tensor([.485, .456, .406], device="cuda")[None, :, None, None]
    std = torch.tensor([.229, .224, .225], device="cuda")[None, :, None, None]
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16):
        for _ in range(2):
            model(torch.zeros(32, 3, 224, 224, device="cuda"))
    torch.cuda.synchronize()
    rows = []
    for row in chosen:
        start = time.perf_counter()
        context_prepare(row)
        context_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        regions, valid, length, boxes = prepare(row)
        detail_ms = (time.perf_counter() - start) * 1000
        torch.cuda.synchronize()
        start = time.perf_counter()
        images = torch.from_numpy(regions.reshape(-1, 224, 224, 3).transpose(0, 3, 1, 2)).cuda()
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16):
            for batch_images in images.split(32):
                model((batch_images.float() / 255 - mean) / std).float().cpu().numpy()
        torch.cuda.synchronize()
        rows.append({
            "group": "/".join([row["split"], row["sample_id"]]), "frames": length,
            "context_prepare_ms": context_ms, "detail_prepare_ms": detail_ms,
            "detail_transfer_encode_copy_ms": (time.perf_counter() - start) * 1000,
        })
    return {
        "scope": "Eight deterministic training groups, warm local files. Sequential timings, no worker overlap; excludes pose estimation, tracking and X3D/head forwards.",
        "views": rows,
        "median_ms": {k: statistics.median(r[k] for r in rows) for k in rows[0] if k.endswith("_ms")},
    }


def mvit_probe(mode):
    """Architecture capacity only; random weights avoid downloading a checkpoint."""
    from torchvision.models.video import mvit_v2_s
    model = optimizer = x = loss = output = None
    result = {"model": "mvit_v2_s", "mode": mode, "weights": "random; capacity only",
              "frames": 16, "size": 224, "batch": 1}
    try:
        torch.manual_seed(20261102)
        model = mvit_v2_s(weights=None)
        model.head[-1] = torch.nn.Linear(model.head[-1].in_features, 3)
        model = model.cuda().eval()
        if mode == "frozen_forward":
            model.requires_grad_(False)
        else:
            model.train()
            optimizer = torch.optim.AdamW(model.parameters(), lr=1e-5)
        scaler = torch.amp.GradScaler("cuda")
        x = torch.randn(1, 3, 16, 224, 224, device="cuda")
        torch.cuda.reset_peak_memory_stats()
        times = []
        for step in range(5):
            torch.cuda.synchronize()
            start = time.perf_counter()
            if optimizer is not None:
                optimizer.zero_grad(set_to_none=True)
            with torch.set_grad_enabled(optimizer is not None), torch.autocast("cuda", dtype=torch.float16):
                output = model(x)
                loss = output.float().square().mean()
            if optimizer is not None:
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
            torch.cuda.synchronize()
            if step >= 2:
                times.append((time.perf_counter() - start) * 1000)
        result.update(fits=True, median_step_ms=statistics.median(times),
                      peak_allocated_mib=torch.cuda.max_memory_allocated() / 2**20,
                      peak_reserved_mib=torch.cuda.max_memory_reserved() / 2**20)
    except torch.cuda.OutOfMemoryError:
        result.update(fits=False, reason="CUDA out of memory under 70% allocator cap")
    finally:
        model = optimizer = x = loss = output = None
        gc.collect()
        torch.cuda.empty_cache()
    return result


def main():
    torch.set_num_threads(2)
    torch.cuda.set_per_process_memory_fraction(.7)
    result = {
        "date": "2026-11-02", "python": platform.python_version(), "torch": torch.__version__,
        "gpu": torch.cuda.get_device_name(),
        "gpu_total_mib": torch.cuda.get_device_properties(0).total_memory / 2**20,
        "allocator_cap_fraction": .7,
        "scope": "Synthetic AMP forward/backward/AdamW on pretrained X3D blocks 0-4 plus adaptive pooling and a new 3-output head; all non-BN backbone parameters trainable. Seven steps per case, first two excluded from median. Capacity probe only, no accuracy inference, input/decode excluded. Peak allocator memory excludes other processes and CUDA context.",
        "training_probes": [],
    }
    for frames, size, batch in [(13, 160, 1), (13, 160, 2), (16, 224, 1), (32, 224, 1)]:
        probe = training_probe(frames, size, batch)
        result["training_probes"].append(probe)
        print(json.dumps(probe), flush=True)
    result["mvit_capacity_probes"] = []
    for mode in ["frozen_forward", "full_gradient_step"]:
        probe = mvit_probe(mode)
        result["mvit_capacity_probes"].append(probe)
        print(json.dumps(probe), flush=True)
    result["real_input_probe"] = real_input_probe()
    result["nvidia_smi_after"] = subprocess.check_output([
        "nvidia-smi", "--query-gpu=temperature.gpu,power.draw,power.limit,memory.used",
        "--format=csv,noheader"
    ], text=True).strip()
    output = ROOT / "research/efficiency_audit_2026-11-02.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(str(output), flush=True)


if __name__ == "__main__":
    main()
