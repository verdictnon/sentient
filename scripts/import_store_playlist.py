"""Register the acquired UNEJ recordings without moving existing catalogue entries."""

import json
from pathlib import Path

from app.p1.catalog import Catalog
from app.p1.geometry import load_geometry, save_geometry
from app.p1.media import register
from app.storage import canonical

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT / "data/public_retail_hd/unej_playlist"
    manifest = json.loads((folder / "video-manifest.json").read_text())
    catalog = Catalog(ROOT / "runs/review_workspace")
    originals = [v for v in catalog.videos() if v["camera_id"] == "carr-unej-overhead"]
    geometry = (
        load_geometry(catalog, originals[0]["id"])["geometry"] if originals else None
    )
    receipt = []
    for item in manifest["videos"]:
        if item["location_name"] != "UNEJ Mart":
            raise ValueError("Rejecting mixed-store playlist")
        video = register(
            catalog,
            Path(item["path"]),
            store_id="carr-unej",
            camera_id="carr-unej-overhead",
            stream_session_id="playlist-" + item["video_id"],
            collection_session="carr/research/playlist/" + item["video_id"],
            display_name="UNEJ Mart · " + item["video_id"],
        )
        existing = load_geometry(catalog, video["id"])
        if geometry and not existing["geometry"]["shapes"]:
            save_geometry(catalog, video["id"], geometry)
        receipt.append(
            {
                "video_id": video["id"],
                "publisher_video_id": item["video_id"],
                "location": item["location_name"],
                "frames": item["frames"],
                "duration_seconds": item["frames"] / 15,
                "source_sha256": item["sha256"],
            }
        )
    with catalog.connect(write=True) as db:
        db.execute(
            "INSERT OR IGNORE INTO ops_store_profiles VALUES (?,?)",
            ("carr-unej", canonical({"name": "UNEJ Mart"})),
        )
    (folder / "import-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(
        json.dumps(
            {
                "registered": len(receipt),
                "seconds": sum(r["duration_seconds"] for r in receipt),
                "store": "UNEJ Mart",
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
