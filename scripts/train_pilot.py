"""Reproducible frozen-X3D engineering baseline, not a continuous-event benchmark."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor/pytorchvideo"))
CLASSES = ["take", "put", "touch", "background"]


def cache_signature():
    return {"preprocessing_version":1,
            "checkpoint_sha256":hashlib.sha256((ROOT/"models/X3D_S.pyth").read_bytes()).hexdigest(),
            "manifest_sha256":{split:hashlib.sha256((ROOT/"data/retailaction"/split/"manifest.json").read_bytes()).hexdigest()
                               for split in ["train","validation"]}}


def seconds(value):
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def time_indices(timestamps, segment_start, start, end, count=13):
    """Nearest actual capture timestamps, not MP4 nominal cadence."""
    import numpy as np
    if not timestamps:
        raise ValueError("missing_frame_timestamps")
    times = np.array([seconds(t) - seconds(segment_start) for t in timestamps])
    if not np.isfinite(times).all() or (np.diff(times) < 0).any():
        raise ValueError("invalid_frame_timestamps")
    if end <= start:
        raise ValueError("invalid_window")
    requested = np.linspace(max(start, times[0]), min(end, times[-1]), count)
    if requested[-1] <= requested[0]:
        raise ValueError("window_outside_capture")
    indices = np.abs(times[:, None] - requested[None, :]).argmin(axis=0)
    if len(set(indices.tolist())) < 4:
        raise ValueError("fewer_than_four_unique_frames")
    return indices.tolist(), times.tolist()


def windows(content, width=2.6):
    segment = content["segment_info"]
    duration = seconds(segment["sampled_at_end"]) - seconds(segment["sampled_at_start"])
    if duration <= 0:
        raise ValueError("invalid_segment_duration")
    events = []
    for event in content.get("labels", {}).get("action", []):
        label = event["label"].lower()
        start, end = float(event["start"]), float(event["end"])
        if label not in CLASSES[:3] or not (0 <= start < end <= 1):
            raise ValueError("invalid_action_annotation")
        events.append((start * duration, end * duration, label))

    def centered(center):
        start = max(0.0, min(center - width / 2, duration - width))
        return start, min(duration, start + width)

    output = []
    for start, end, label in events:
        left, right = centered((start + end) / 2)
        overlaps = [e for e in events if e[0] < right and e[1] > left]
        if len(overlaps) == 1:
            output.append({"start": left, "end": right, "label": label,
                           "window_source": "annotation_centered"})
    # At most one background window, with a guard interval around every event.
    candidates = [duration / 2] if not events else [width / 2, duration - width / 2]
    for center in candidates:
        left, right = centered(center)
        if all(right <= start - .3 or left >= end + .3 for start, end, _ in events):
            output.append({"start": left, "end": right, "label": "background",
                           "window_source": "annotated_subject_non_event"})
            break
    return output


def pose_bounds(poses, indices):
    """RetailAction pose values are [row, col]; never use action-point labels."""
    points = []
    for index in sorted(set(indices)):
        if not poses or index >= len(poses) or not isinstance(poses[index], dict):
            continue
        record = poses[index]
        for joint, coordinates in record.get("pose", {}).items():
            if (isinstance(coordinates, list) and len(coordinates) == 2
                    and record.get("joint_scores", {}).get(joint, 0) >= .2):
                row, col = coordinates
                if 0 <= row <= 1 and 0 <= col <= 1:
                    points.append((col, row))
    if len(points) < 4:
        raise ValueError("insufficient_subject_pose")
    return min(x for x,y in points), min(y for x,y in points), max(x for x,y in points), max(y for x,y in points)


def tensor_for_window(frames, camera, segment_start, window):
    import numpy as np
    from PIL import Image
    import torch
    indices, times = time_indices(camera.get("frame_timestamps"), segment_start,
                                  window["start"], window["end"])
    if len(times) != len(frames):
        raise ValueError("decoded_frame_timestamp_count_mismatch")
    x0, y0, x1, y1 = pose_bounds(camera.get("poses"), indices)
    height, width = frames[0].shape[:2]
    side = max((x1-x0)*width, (y1-y0)*height, min(width,height)*.25) * 1.4
    cx, cy = (x0+x1)*width/2, (y0+y1)*height/2
    crop = (round(cx-side/2), round(cy-side/2), round(cx+side/2), round(cy+side/2))
    # Repeat selection only when sparse source frames require it; no synthetic interpolation.
    resized = {index: np.asarray(Image.fromarray(frames[index]).crop(crop).resize(
        (160,160), Image.Resampling.BILINEAR)).copy() for index in set(indices)}
    video = torch.from_numpy(np.stack([resized[index] for index in indices])).permute(3,0,1,2).float()/255
    mean = torch.tensor([.45,.45,.45])[:,None,None,None]
    std = torch.tensor([.225,.225,.225])[:,None,None,None]
    return (video-mean)/std, {"unique_frames": len(set(indices)), "crop_xyxy": crop}


def decode(path):
    import av
    with av.open(str(path)) as container:
        container.streams.video[0].thread_type = "AUTO"
        return [frame.to_ndarray(format="rgb24") for frame in container.decode(video=0)]


def extract_features(args, output):
    import numpy as np
    import torch
    from pytorchvideo.models.hub.x3d import x3d_s
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        raise RuntimeError("CUDA unavailable; fix the local environment before an expensive CPU extraction")
    torch.set_num_threads(4)
    torch.cuda.set_per_process_memory_fraction(.65)
    model = x3d_s(pretrained=False)
    checkpoint = ROOT / "models/X3D_S.pyth"
    if not checkpoint.exists():
        raise FileNotFoundError(checkpoint)
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    model.load_state_dict(state["model_state"], strict=True)
    head = model.blocks[-1]
    feature_dim = head.proj.in_features
    head.proj = torch.nn.Identity()
    head.activation = torch.nn.Identity()
    model = model.eval().requires_grad_(False).to(device)
    torch.cuda.reset_peak_memory_stats()
    # Use cached features on subsequent head-training runs.
    partial = output / "features.partial.pt"
    resume = torch.load(partial,weights_only=True,map_location="cpu") if partial.exists() else None
    if resume is not None and resume.get("cache_signature") != cache_signature():
        resume = None
    features = list(resume["features"].unbind()) if resume is not None else []
    rows = resume["rows"] if resume is not None else []
    skipped = resume["skipped"] if resume is not None else []
    processed = set(resume["processed"]) if resume is not None else set()
    previous_seconds = resume["feature_seconds"] if resume is not None else 0.0
    previous_allocated = resume.get("peak_cuda_allocated_mib",0.0) if resume is not None else 0.0
    previous_reserved = resume.get("peak_cuda_reserved_mib",0.0) if resume is not None else 0.0
    new_samples = 0
    started = time.monotonic()

    def save_partial():
        state = {"features":torch.stack(features) if features else torch.empty(0,feature_dim),
                 "rows":rows,"skipped":skipped,"processed":sorted(processed),
                 "cache_signature":cache_signature(),
                 "peak_cuda_allocated_mib":max(previous_allocated,torch.cuda.max_memory_allocated()/1024**2),
                 "peak_cuda_reserved_mib":max(previous_reserved,torch.cuda.max_memory_reserved()/1024**2),
                 "feature_seconds":previous_seconds+time.monotonic()-started}
        temporary = output / "features.partial.tmp.pt"
        torch.save(state,temporary);temporary.replace(partial)

    for split in ["train", "validation"]:
        base = ROOT / "data/retailaction" / split
        manifest = json.loads((base / "manifest.json").read_text())
        ids = sorted(manifest["samples"])
        for ordinal, sample in enumerate(ids):
            key = f"{split}/{sample}"
            if key in processed:
                continue
            processed.add(key);new_samples += 1
            folder = base / sample
            try:
                content = json.loads((folder / "metadata.json").read_text())["content"]
                sample_windows = windows(content)
            except (ValueError, KeyError, TypeError) as error:
                skipped.append({"split":split,"sample":sample,"reason":str(error)})
                continue
            for view in ["rank0", "rank1"]:
                try:
                    camera = content["action_cam"][view]
                    if not camera.get("frame_timestamps"):
                        raise ValueError("missing_frame_timestamps")
                    frames = decode(folder / f"{view}_video.mp4")
                    for window_number, window in enumerate(sample_windows):
                        try:
                            tensor, quality = tensor_for_window(frames, camera,
                                content["segment_info"]["sampled_at_start"], window)
                            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16):
                                feature = model(tensor[None].to(device)).float().cpu()[0]
                            if feature.shape != (feature_dim,) or not torch.isfinite(feature).all():
                                raise ValueError("invalid_backbone_features")
                            features.append(feature)
                            rows.append({"split":split,"sample":sample,"view":view,
                                         "window_number":window_number,**window,**quality})
                        except (ValueError, KeyError, TypeError) as error:
                            skipped.append({"split":split,"sample":sample,"view":view,"reason":str(error)})
                except (ValueError, KeyError, TypeError, OSError) as error:
                    skipped.append({"split":split,"sample":sample,"view":view,"reason":str(error)})
            if (ordinal+1) % 32 == 0:
                save_partial()
                print(f"features {split}: {ordinal+1}/{len(ids)} samples, {len(rows)} windows, {time.monotonic()-started:.1f}s", flush=True)
            if args.extract_chunk and new_samples >= args.extract_chunk:
                save_partial()
                print(f"Saved resumable extraction: {len(processed)} samples complete",flush=True)
                return None
    if not features:
        raise RuntimeError("No usable windows")
    bundle = {"features":torch.stack(features),"rows":rows,"skipped":skipped,"cache_signature":cache_signature(),
              "backbone":"x3d_s","backbone_sha256":hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
              "feature_seconds":previous_seconds+time.monotonic()-started,
              "peak_cuda_allocated_mib":max(previous_allocated,torch.cuda.max_memory_allocated()/1024**2),
              "peak_cuda_reserved_mib":max(previous_reserved,torch.cuda.max_memory_reserved()/1024**2),
              "gpu":torch.cuda.get_device_name(),"torch_version":str(torch.__version__)}
    torch.save(bundle, output / "features.pt")
    partial.unlink(missing_ok=True)
    (output / "windows.json").write_text(json.dumps({"rows":rows,"skipped":skipped},indent=2)+"\n")
    return bundle


def metrics(truth, predictions, class_names=None):
    import torch
    names = class_names if class_names is not None else CLASSES
    confusion = torch.zeros(len(names),len(names),dtype=torch.int64)
    for target,prediction in zip(truth.tolist(),predictions.tolist()):
        confusion[target,prediction] += 1
    results = {}
    for index,label in enumerate(names):
        tp = int(confusion[index,index]); support = int(confusion[index].sum())
        predicted = int(confusion[:,index].sum())
        precision = tp/predicted if predicted else 0.0
        recall = tp/support if support else None
        f1 = 2*precision*recall/(precision+recall) if recall is not None and precision+recall else 0.0
        results[label] = {"support":support,"precision":precision,"recall":recall,"f1":f1 if support else None}
    supported = [value["f1"] for value in results.values() if value["support"]]
    return {"accuracy":float((truth==predictions).float().mean()),
            "macro_f1_supported":sum(supported)/len(supported),"per_class":results,
            "confusion_matrix":confusion.tolist()}


def train_head(args, bundle, output):
    import numpy as np
    import torch
    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    torch.set_num_threads(4)
    rows = bundle["rows"]
    train = torch.tensor([r["split"]=="train" for r in rows])
    validation = ~train
    if not train.any() or not validation.any():
        raise RuntimeError("Both official splits need usable windows")
    targets = torch.tensor([CLASSES.index(r["label"]) for r in rows])
    raw = bundle["features"]
    mean = raw[train].mean(0); std = raw[train].std(0).clamp_min(.01)
    features = ((raw-mean)/std).clamp(-10,10)
    x, y = features[train], targets[train]
    vx, vy = features[validation], targets[validation]
    counts = torch.bincount(y,minlength=4)
    weights = torch.where(counts>0,counts.float().clamp_min(1).rsqrt(),0)
    weights = weights / weights[weights>0].mean()
    loss_function = torch.nn.CrossEntropyLoss(weight=weights)
    # Verify that the supervised path can fit a tiny subset before the pilot fit.
    tiny_x, tiny_y = x[:16], y[:16]
    tiny_head = torch.nn.Linear(x.shape[1],4)
    tiny_optimizer = torch.optim.AdamW(tiny_head.parameters(),lr=.01)
    for _ in range(150):
        tiny_optimizer.zero_grad(); tiny_loss = loss_function(tiny_head(tiny_x),tiny_y)
        tiny_loss.backward(); tiny_optimizer.step()
    tiny_accuracy = float((tiny_head(tiny_x).argmax(1)==tiny_y).float().mean())
    if tiny_accuracy < .95:
        raise RuntimeError(f"Tiny-subset fit failed: {tiny_accuracy}")
    torch.manual_seed(args.seed)
    head = torch.nn.Linear(x.shape[1],4)
    optimizer = torch.optim.AdamW(head.parameters(),lr=.001,weight_decay=.01)
    best, best_score, best_epoch, history = None, -1.0, 0, []
    started = time.monotonic()
    for epoch in range(1,args.epochs+1):
        order = torch.randperm(len(x)); head.train(); total_loss=0.0
        for batch in order.split(64):
            optimizer.zero_grad(); loss = loss_function(head(x[batch]),y[batch]); loss.backward()
            optimizer.step(); total_loss += float(loss.detach()) * len(batch)
        head.eval()
        with torch.no_grad(): result = metrics(vy,head(vx).argmax(1))
        history.append({"epoch":epoch,"loss":total_loss/len(x),"validation_macro_f1":result["macro_f1_supported"]})
        if result["macro_f1_supported"] > best_score:
            best_score = result["macro_f1_supported"];best_epoch = epoch
            best = {name:value.detach().clone() for name,value in head.state_dict().items()}
        if epoch % 10 == 0:
            print(f"head epoch {epoch}: loss={total_loss/len(x):.4f} val_macro_f1={result['macro_f1_supported']:.3f}",flush=True)
    head.load_state_dict(best)
    with torch.no_grad():
        result = metrics(vy,head(vx).argmax(1))
        train_result = metrics(y,head(x).argmax(1))
    predictions = head(vx).argmax(1)
    # Secondary view fusion, same held-out sample/window, no extra fitting.
    grouped = {}
    with torch.no_grad():
        for row,logits,target in zip([r for r in rows if r["split"]=="validation"],head(vx),vy):
            key=(row["sample"],row["window_number"])
            grouped.setdefault(key,{"logits":[],"target":int(target)})["logits"].append(logits)
    fused_truth = torch.tensor([g["target"] for g in grouped.values()])
    fused_pred = torch.stack([torch.stack(g["logits"]).mean(0) for g in grouped.values()]).argmax(1)
    majority = int(counts.argmax())
    metadata = {k:v for k,v in bundle.items() if k not in ["features","rows","skipped"]}
    report = {"status":"completed_engineering_pilot","seed":args.seed,"classes":CLASSES,
              "scope":"Annotation-centered, pose-conditioned clip classifier on bounded archive-prefix subsets; not continuous event/theft detection.",
              **metadata,"train_windows":len(y),"validation_windows":len(vy),
              "train_class_counts":{c:int(counts[i]) for i,c in enumerate(CLASSES)},
              "skipped_reasons":dict(Counter(r["reason"] for r in bundle["skipped"])),
              "tiny_overfit_accuracy":tiny_accuracy,"best_epoch":best_epoch,"epochs":args.epochs,
              "head_training_seconds":time.monotonic()-started,
              "train":train_result,"validation":result,"validation_fused":metrics(fused_truth,fused_pred),
              "majority_validation":metrics(vy,torch.full_like(vy,majority)),
              "limitations":["Not representative sampling; no untouched test-set evaluation.",
                             "Validation used to select the epoch; not a final unbiased quality estimate.",
                             "Uses supplied poses and known event intervals; not deployment detection performance.",
                             "Sparse minority classes and paired views reduce effective independent sample size."]}
    torch.save({"state_dict":best,"feature_mean":mean,"feature_std":std,"classes":CLASSES,
                "feature_clip":10,"backbone_sha256":bundle["backbone_sha256"],
                "pytorchvideo_revision":"2ac6ed361309cb25144636ad82dd8bd6a4970e0c",
                "preprocessing":{"frames":13,"size":160,"window_seconds":2.6,"pose_order":"row,col"},
                "report":report},output / "head.pt")
    (output / "metrics.json").write_text(json.dumps(report,indent=2)+"\n")
    (output / "history.json").write_text(json.dumps(history,indent=2)+"\n")
    print(json.dumps({"train_windows":len(y),"validation_windows":len(vy),
                      "class_counts":report["train_class_counts"],"validation":result,
                      "peak_cuda_allocated_mib":report["peak_cuda_allocated_mib"]},indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",default="runs/x3d_s_pilot")
    parser.add_argument("--epochs",type=int,default=60);parser.add_argument("--seed",type=int,default=42)
    parser.add_argument("--extract-chunk",type=int,default=0,help="Checkpoint and exit after this many new samples; rerun to resume")
    parser.add_argument("--reextract",action="store_true");args=parser.parse_args()
    output=ROOT/args.output;output.mkdir(parents=True,exist_ok=True)
    import torch
    cache=output/"features.pt"
    bundle=torch.load(cache,weights_only=True,map_location="cpu") if cache.exists() and not args.reextract else None
    if bundle is None or bundle.get("cache_signature") != cache_signature():
        bundle=extract_features(args,output)
    if bundle is None:
        return
    train_head(args,bundle,output)


if __name__=="__main__":
    main()
