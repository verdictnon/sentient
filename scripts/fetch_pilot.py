"""Stream a bounded archive prefix; retain complete samples and their provenance."""
import argparse
import concurrent.futures
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "49cb590723db921a4bd5a38adea92f6abd3f7a00"
BASE = f"https://huggingface.co/datasets/standard-cognition/RetailAction/resolve/{REVISION}"
WANTED = {"metadata.json", "rank0_video.mp4", "rank1_video.mp4"}


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={
        "User-Agent": "Sentient-pilot/0.1", "Accept-Encoding": "identity"
    }), timeout=60)


def fetch_split(split, limit):
    destination = ROOT / "data/retailaction" / split
    destination.mkdir(parents=True, exist_ok=True)
    selected, complete = {}, set()
    started = time.monotonic()
    url = f"{BASE}/data/{split}.tar?download=true"
    print(f"{split}: streaming up to {limit} complete samples", flush=True)
    with get(url) as response:
        content_range = response.headers.get("Content-Range", "")
        if content_range and not content_range.startswith("bytes 0-"):
            raise RuntimeError(f"Unexpected range: {content_range}")
        with tarfile.open(fileobj=io.BufferedReader(response, 1024 * 1024), mode="r|") as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if not member.isfile() or path.name not in WANTED:
                    continue
                if len(path.parts) != 3 or path.parts[0] != split or not path.parts[1].isdigit():
                    raise ValueError(f"Unexpected archive path: {member.name}")
                sample = path.parts[1]
                if sample not in selected and len(selected) >= limit:
                    continue
                if member.size > 100 * 1024 * 1024:
                    raise ValueError(f"Unexpected member size: {member.name}")
                records = selected.setdefault(sample, {})
                local = destination / sample / path.name
                local.parent.mkdir(exist_ok=True)
                stream = archive.extractfile(member)
                payload = stream.read()
                if len(payload) != member.size:
                    raise IOError(f"Truncated member: {member.name}")
                digest = hashlib.sha256(payload).hexdigest()
                if not local.exists() or hashlib.sha256(local.read_bytes()).hexdigest() != digest:
                    temporary = local.with_suffix(local.suffix + ".partial")
                    temporary.write_bytes(payload)
                    temporary.replace(local)
                records[path.name] = {"bytes": len(payload), "sha256": digest}
                if set(records) == WANTED:
                    complete.add(sample)
                    if len(complete) % 64 == 0:
                        print(f"{split}: {len(complete)}/{limit} complete ({time.monotonic()-started:.1f}s)", flush=True)
                    if len(complete) >= limit:
                        break
    if len(complete) != limit:
        raise RuntimeError(f"{split}: only {len(complete)} complete samples")
    manifest = {
        "source": url, "revision": REVISION, "split": split,
        "selection": "First complete samples in archive order; engineering pilot, not representative sampling.",
        "sample_count": len(complete), "elapsed_seconds": time.monotonic() - started,
        "samples": {sample: selected[sample] for sample in sorted(complete)},
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {"split": split, "samples": len(complete), "seconds": round(manifest["elapsed_seconds"], 1)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=int, default=512)
    parser.add_argument("--validation", type=int, default=128)
    args = parser.parse_args()
    target = ROOT / "data/retailaction"
    target.mkdir(parents=True, exist_ok=True)
    with get(f"{BASE}/LICENSE") as response:
        (target / "LICENSE").write_bytes(response.read())
    (target / "NOTICE").write_text(
        "RetailAction dataset by Standard Cognition.\n"
        "This Dataset is licensed under the Standard.AI Dataset License, Copyright © Standard Corp. All Rights Reserved.\n"
        "Research pilot only. See LICENSE for attribution, deidentification and commercial conditions.\n"
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(fetch_split, split, count) for split, count in
                   [("train", args.train), ("validation", args.validation)]]
        for future in concurrent.futures.as_completed(futures):
            print(json.dumps(future.result()), flush=True)


if __name__ == "__main__":
    main()
