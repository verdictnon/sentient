"""Fetch bounded contiguous 720p clips from one publisher-labelled store."""

import csv
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import scripts.acquire_carr as carr
from scripts.acquire_carr import DOI, ROOT, acquire, contiguous_runs, encode, save

NETWORK = ThreadPoolExecutor(max_workers=8)


class PlaylistRanges(carr.RangeFile):
    def fetch(self, start, size):
        for path in self.cache.glob("*.bin"):
            try:
                begin, length = map(int, path.stem.split("-"))
            except ValueError:
                continue
            if (
                begin <= start
                and start + size <= begin + length
                and path.stat().st_size == length
            ):
                with path.open("rb") as stream:
                    stream.seek(start - begin)
                    return stream.read(size)
        if size <= 2 * 1024**2:
            return super().fetch(start, size)
        pieces = [
            (offset, min(2 * 1024**2, start + size - offset))
            for offset in range(start, start + size, 2 * 1024**2)
        ]
        base = super().fetch
        jobs = [NETWORK.submit(base, offset, length) for offset, length in pieces]
        return b"".join(job.result() for job in jobs)


def main():
    source = ROOT / "data/public_retail_hd/carr"
    target = ROOT / "data/public_retail_hd/unej_playlist"
    target.mkdir(parents=True, exist_ok=True)
    for name in [
        "CARR_annotations.zip",
        "CARR_splits.zip",
        "images",
        "labels",
        "range_cache",
    ]:
        path = target / name
        if not path.exists():
            path.symlink_to(
                (source / name).resolve(), target_is_directory=(source / name).is_dir()
            )
    groups = defaultdict(list)
    with (source / "carr_manifest.csv").open() as f:
        for row in csv.DictReader(f):
            if row["location_name"] == "UNEJ Mart" and row["video_id"] != "V0797":
                groups[row["video_id"]].append(row)
    candidates = sorted(
        ((max(contiguous_runs(rows), key=len), vid) for vid, rows in groups.items()),
        key=lambda pair: (-len(pair[0]), pair[1]),
    )[:10]
    items = []
    wanted = {}
    for run, vid in candidates:
        rows = run[:120]
        first = rows[0]
        item = {
            k: first[k]
            for k in [
                "video_id",
                "class_id",
                "class_name",
                "participant_code",
                "location_name",
            ]
        }
        item.update(
            demo_frames=rows,
            diagnostic_samples=[],
            native_role="qualitative_playlist",
            use="Source-paced same-store playlist; no accuracy claims",
            original_video_frames=len(groups[vid]),
            longest_contiguous_frames=len(run),
        )
        items.append(item)
        wanted.update({r["file_name"] + ".jpg": r for r in rows})
    protocol = {
        "format": "sentient.store-playlist/1",
        "doi": DOI,
        "native_frame_rate": 15,
        "timing_basis": "Publisher-declared contiguous frame indices at 15 fps; separate clips have no common capture clock",
        "location_name": "UNEJ Mart",
        "selection_rule": "Ten longest contiguous sequences, up to 120 frames each; exclude the already installed V0797; selected without model scores",
        "videos": items,
        "unique_images": len(wanted),
    }
    save(target / "protocol.json", protocol)
    metadata = json.loads(
        (ROOT / "runs/full_audit_2026-11-03/carr-dataverse-metadata.json").read_text()
    )
    carr.RangeFile = PlaylistRanges
    acquire(target, metadata, prepared=(protocol, wanted))
    videos = encode(target, protocol)
    print(
        json.dumps(
            {
                "clips": len(videos),
                "seconds": sum(v["frames"] / 15 for v in videos),
                "location": "UNEJ Mart",
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
