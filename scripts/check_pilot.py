"""Reload saved artifacts, profile real inference, and fit a two-class diagnostic."""
import json
from pathlib import Path
import statistics
import time
import torch
from train_pilot import ROOT, CLASSES, decode, metrics, tensor_for_window
from pytorchvideo.models.hub.x3d import x3d_s

OUT = ROOT / "runs/x3d_s_pilot"
torch.set_num_threads(4)
bundle = torch.load(OUT/"features.pt",weights_only=True)
artifact = torch.load(OUT/"head.pt",weights_only=True)
head = torch.nn.Linear(bundle["features"].shape[1],len(CLASSES))
head.load_state_dict(artifact["state_dict"]);head.eval()
validation = torch.tensor([r["split"]=="validation" for r in bundle["rows"]])
truth = torch.tensor([CLASSES.index(r["label"]) for r in bundle["rows"]])[validation]
with torch.no_grad():
    x=((bundle["features"][validation]-artifact["feature_mean"])/artifact["feature_std"]).clamp(-10,10)
    replay = metrics(truth,head(x).argmax(1))
assert replay == artifact["report"]["validation"], "Checkpoint reload changed predictions"

# Profile a real preprocessed validation crop; reset allocator peaks before inference.
row=next(r for r in bundle["rows"] if r["split"]=="validation")
folder=ROOT/"data/retailaction/validation"/row["sample"]
content=json.loads((folder/"metadata.json").read_text())["content"]
tensor,_=tensor_for_window(decode(folder/f"{row['view']}_video.mp4"),content["action_cam"][row["view"]],
    content["segment_info"]["sampled_at_start"],row)
model=x3d_s(pretrained=False)
model.load_state_dict(torch.load(ROOT/"models/X3D_S.pyth",weights_only=True,map_location="cpu")["model_state"])
model.blocks[-1].proj=torch.nn.Identity();model.blocks[-1].activation=torch.nn.Identity()
model=model.eval().requires_grad_(False).cuda();head=head.cuda()
tensor=tensor[None].cuda();mean=artifact["feature_mean"].cuda();std=artifact["feature_std"].cuda()
def forward():
    with torch.inference_mode(),torch.autocast("cuda",dtype=torch.float16): feature=model(tensor)
    with torch.inference_mode(): return head(((feature.float()-mean)/std).clamp(-10,10))
for _ in range(5): forward()
torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats()
timings=[]
for _ in range(20):
    start=torch.cuda.Event(enable_timing=True);end=torch.cuda.Event(enable_timing=True)
    start.record();forward();end.record();end.synchronize();timings.append(start.elapsed_time(end))
profile={"checkpoint_reload_matches":True,"input_shape":list(tensor.shape),"batch_size":1,
    "median_forward_ms":statistics.median(timings),"p95_forward_ms":sorted(timings)[18],
    "peak_cuda_allocated_mib":torch.cuda.max_memory_allocated()/1024**2,
    "peak_cuda_reserved_mib":torch.cuda.max_memory_reserved()/1024**2,
    "scope":"20 warm single-window backbone+head GPU forwards; excludes decode, crop, stream ingestion and CUDA context memory."}
(OUT/"profile.json").write_text(json.dumps(profile,indent=2)+"\n")
bundle.pop("peak_cuda_allocated_mib",None);bundle.pop("peak_cuda_reserved_mib",None)
bundle["inference_profile"]=profile
bundle["memory_note"]="Extraction peak was not preserved across all initial chunks; see labeled inference profile."
torch.save(bundle,OUT/"features.pt")
# Replace the last resumed process's model-only memory figure with a labeled profile.
report=json.loads((OUT/"metrics.json").read_text())
report.pop("peak_cuda_allocated_mib",None);report.pop("peak_cuda_reserved_mib",None)
report["inference_profile"]=profile
report["memory_note"]="Extraction peak was not preserved across all resumed chunks; use the separately labeled inference profile."
(OUT/"metrics.json").write_text(json.dumps(report,indent=2)+"\n")
artifact["report"]=report;torch.save(artifact,OUT/"head.pt")
del model,head,tensor;torch.cuda.empty_cache()

# Supported-task diagnostic; omit put/touch rather than pretending to evaluate them.
names=["take","background"]
keep=torch.tensor([r["label"] in names for r in bundle["rows"]])
rows=[r for r in bundle["rows"] if r["label"] in names]
raw=bundle["features"][keep]
training=torch.tensor([r["split"]=="train" for r in rows]);valid=~training
y=torch.tensor([names.index(r["label"]) for r in rows])
mean=raw[training].mean(0);std=raw[training].std(0).clamp_min(.01)
x=((raw-mean)/std).clamp(-10,10)
torch.manual_seed(42);head=torch.nn.Linear(x.shape[1],2)
optimizer=torch.optim.AdamW(head.parameters(),lr=.001,weight_decay=.01)
counts=torch.bincount(y[training],minlength=2).float()
weights=counts.rsqrt();weights/=weights.mean()
criterion=torch.nn.CrossEntropyLoss(weight=weights)
best_score=-1;best=None;best_epoch=0;started=time.monotonic()
for epoch in range(1,61):
    for ids in torch.randperm(int(training.sum())).split(64):
        optimizer.zero_grad();loss=criterion(head(x[training][ids]),y[training][ids]);loss.backward();optimizer.step()
    with torch.no_grad(): result=metrics(y[valid],head(x[valid]).argmax(1),names)
    if result["macro_f1_supported"]>best_score:
        best_score=result["macro_f1_supported"];best_epoch=epoch
        best={k:v.detach().clone() for k,v in head.state_dict().items()}
head.load_state_dict(best)
with torch.no_grad(): result=metrics(y[valid],head(x[valid]).argmax(1),names)
binary={"classes":names,"train_windows":int(training.sum()),"validation_windows":int(valid.sum()),
    "best_epoch":best_epoch,"epochs":60,"training_seconds":time.monotonic()-started,
    "validation":result,"majority_validation":metrics(y[valid],torch.full_like(y[valid],int(counts.argmax())),names),
    "limitations":["Excludes put/touch examples; not a replacement four-class model.",
                   "Annotation-centered, pose-conditioned clips; not continuous-event performance.",
                   "Small correlated views; validation also selected the epoch; test split untouched."]}
(OUT/"binary_metrics.json").write_text(json.dumps(binary,indent=2)+"\n")
torch.save({"state_dict":best,"feature_mean":mean,"feature_std":std,"feature_clip":10,"classes":names,
    "backbone_sha256":bundle["backbone_sha256"],"report":binary},OUT/"take_background_head.pt")
print(json.dumps({"profile":profile,"four_class_validation_macro_f1":replay["macro_f1_supported"],
                  "binary":binary},indent=2))
