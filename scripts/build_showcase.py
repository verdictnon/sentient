"""Run a real pose detector and the tracker over a video and save what they found.

    python scripts/build_showcase.py video.mp4 --weights yolov8n-pose.pt \
        --output app/assets/real/store-aisle --name "Store aisle" --store-id aisle-shop \
        --store-name "Aisle shop" --source "..." --source-url "..." --licence "CC BY 4.0"

--output is a path without extension. The MP4 is copied there, re-encoded to H.264
for browsers if needed, with the detections beside it. Add --shelves a.json to save
shelf areas, as [{"id","name","points":[[x,y]...]}] in pixels.
"""

import argparse
import gzip
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--weights", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--store-name", required=True)
    parser.add_argument("--camera-id", default="cam-01")
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--licence", required=True)
    parser.add_argument("--shelves", type=Path)
    parser.add_argument("--sample-ms", type=float, default=200)
    parser.add_argument("--imgsz", type=int, default=960)
    args = parser.parse_args()

    import av
    from ultralytics import YOLO

    from app.p2.config import TrackingConfig
    from app.p2.tracker import ByteTracker, Detection

    model = YOLO(args.weights)
    config = TrackingConfig(sample_period_ms=args.sample_ms)
    tracker = ByteTracker(config)
    frames = []
    started = time.perf_counter()
    with av.open(str(args.video)) as container:
        stream = container.streams.video[0]
        size = [stream.width, stream.height]
        codec = stream.codec_context.name
        due = 0.0
        last_ms = 0.0
        for index, frame in enumerate(container.decode(stream)):
            time_ms = float(frame.time * 1000)
            last_ms = time_ms
            if time_ms + 1e-6 < due:
                continue
            due = time_ms + args.sample_ms
            result = model.predict(frame.to_image(), conf=config.low_threshold, imgsz=args.imgsz, verbose=False)[0]
            boxes = result.boxes.xyxy.tolist()
            scores = result.boxes.conf.tolist()
            points = result.keypoints.xy.tolist() if result.keypoints is not None else [None] * len(boxes)
            people = [Detection(tuple(box), float(score), 0, index) for box, score in zip(boxes, scores)]
            rows = []
            for row in tracker.advance(people, time_ms, tuple(size)):
                observed = row["position_status"] == "observed"
                # Tracker output keeps the matched box, so the pose is found by that box.
                pose = next((p for b, p in zip(boxes, points) if observed and p and max(abs(u - v) for u, v in zip(b, row["box"])) < 1), None)
                rows.append(
                    [
                        int(row["local_track_id"]),
                        *[round(v, 1) for v in row["box"]],
                        round(row["score"], 3),
                        observed,
                        [[round(x), round(y)] for x, y in pose] if pose else None,
                    ]
                )
            frames.append(dict(t=round(time_ms), boxes=rows))
            if len(frames) % 50 == 0:
                print(f"{len(frames)} samples, {time_ms / 1000:.0f} s, {time.perf_counter() - started:.0f} s elapsed", flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    target = args.output.with_suffix(".mp4")
    if codec == "h264":
        shutil.copyfile(args.video, target)
    else:
        raise SystemExit("Convert the video to H.264 first so browsers can play it")
    geometry = None
    if args.shelves:
        shapes = [dict(kind="shelf", product_type="", **shelf) for shelf in json.loads(args.shelves.read_text(encoding="utf-8"))]
        geometry = dict(
            geometry_version=2,
            frame_dims_px=size,
            max_gap_ms=1500,
            active=dict(line=None, zone=None, queue=None, checkout=None),
            shapes=shapes,
        )
    payload = dict(
        name=args.name,
        store_id=args.store_id,
        store_name=args.store_name,
        camera_id=args.camera_id,
        size=size,
        duration_s=round(last_ms / 1000, 2),
        source=args.source,
        source_url=args.source_url,
        licence=args.licence,
        model=dict(detector=Path(args.weights).stem, tracker="ByteTrack", sample_ms=args.sample_ms),
        geometry=geometry,
        frames=frames,
    )
    with gzip.open(args.output.with_suffix(".json.gz"), "wt", encoding="utf-8") as output:
        json.dump(payload, output, separators=(",", ":"))
    people = len({box[0] for frame in frames for box in frame["boxes"]})
    print(f"{target.name}: {len(frames)} samples, {people} tracks, {time.perf_counter() - started:.0f} s")


if __name__ == "__main__":
    main()
