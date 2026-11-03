"""Generate an explicitly synthetic VFR timing/review demo, never real footage."""

import argparse
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path

import av
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
TIMES = [0, 100, 300, 600, 1000, 1400, 1800, 2000, 2400, 3000, 3500, 4000, 4600, 5400, 6200, 7000, 7800]


def make_video(path: Path, *, times=None, width=960, height=540, start_ms=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    times = TIMES if times is None else times
    with av.open(str(path), "w", format="mp4") as output:
        stream = output.add_stream("libx264", rate=10)
        stream.width, stream.height = width, height
        stream.pix_fmt = "yuv420p"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        stream.codec_context.max_b_frames = 0
        stream.codec_context.gop_size = 12
        stream.options = {"crf": "20", "preset": "veryfast"}
        for index, time in enumerate(times):
            image = Image.new("RGB", (width, height), "#172725")
            draw = ImageDraw.Draw(image)
            # A diagrammatic store floor, intentionally not a detector benchmark.
            for x in (.16, .34, .70, .86):
                draw.rounded_rectangle((int(width*x)-width*.045,height*.22,int(width*x)+width*.045,height*.62),
                                       radius=6, fill="#29423c", outline="#4c675c", width=2)
            draw.rectangle((0,height*.76,width,height*.99),fill="#203630")
            anchors = [(0,.1),(1000,.4),(2000,.6),(3000,.7),(4000,.9)]
            x = .9
            for (a, xa), (b, xb) in zip(anchors, anchors[1:]):
                if a <= time <= b:
                    x=xa+(xb-xa)*(time-a)/(b-a)
                    break
            if time == 0:
                x=.1
            cx, cy = width*x, height*.73
            radius=max(3,int(width*.012))
            draw.ellipse((cx-radius,cy-radius,cx+radius,cy+radius),fill="#d2ef96")
            draw.text((width*.04,height*.06),"SYNTHETIC TIMING DEMO / NOT REAL STORE FOOTAGE",fill="#d2ef96")
            draw.text((width*.04,height*.91),f"SOURCE PTS {time+start_ms} ms   |   FRAME {index:03d}   |   NO CAPTURE UTC",fill="#c8d8ce")
            frame=av.VideoFrame.from_image(image)
            frame.pts=time+start_ms
            frame.time_base=Fraction(1,1000)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return path


def make_demo(folder: Path):
    folder.mkdir(parents=True,exist_ok=True)
    video=make_video(folder/"sentient-synthetic-vfr.mp4")
    originals=[json.loads(line) for line in (ROOT/"contracts/v1/examples.jsonl").read_text().splitlines() if line]
    rows=[]
    for original in originals:
        row=deepcopy(original)
        row["record_id"]="p1-demo:"+row["record_id"]
        if row["type"]=="track.observed":
            row["frame_dims_px"]=[960,540]
            row["bbox_xyxy_px"]=[value*(9.6 if i%2==0 else 5.4) for i,value in enumerate(row["bbox_xyxy_px"])]
            row["frame_index"]=TIMES.index(int(row["media_time_ms"]))
        if row["type"]=="action.candidate":
            row["frame_indices"]=[TIMES.index(t) for t in row["frame_media_times_ms"]]
        rows.append(row)
    tracks=folder/"sentient-synthetic-tracks.jsonl"
    tracks.write_text("\n".join(json.dumps(row) for row in rows)+"\n",encoding="utf-8")
    return video,tracks


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/".local/demo")
    args=parser.parse_args()
    for path in make_demo(args.output):
        print(path)


if __name__=="__main__":
    main()


def fixture_geometry(dimensions):
    w, h = dimensions
    return {
        "geometry_version": 1, "frame_dims_px": dimensions, "max_gap_ms": 1500,
        "shapes": [
            {"id": "entrance", "name": "Entrance", "kind": "line", "hysteresis_px": 5,
             "points": [[w * .5, h], [w * .5, 0]]},
            {"id": "sales-floor", "name": "Sales floor", "kind": "zone",
             "points": [[w * .55, 0], [w * .85, 0], [w * .85, h], [w * .55, h]]},
        ],
        "active": {"line": "entrance", "zone": "sales-floor", "queue": None, "checkout": None},
    }

