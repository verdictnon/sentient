"""Autonomous, single-store recorded-camera patrol using the existing live worker."""

import json
import re
import shutil
import threading
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.p1.geometry import load_geometry
from app.storage import canonical

# Publisher location names disambiguate the legacy shared research store ID.
CARR_LOCATIONS = {
    "UNEJ-Mart": ("carr-unej", "UNEJ Mart", "carr-unej-overhead"),
    "carr-unej-overhead": ("carr-unej", "UNEJ Mart", "carr-unej-overhead"),
    "KPRI-Kalimantan": ("carr-kpri-kalimantan", "KPRI Kalimantan", "KPRI-Kalimantan"),
    "Budi-Jaya-Store": ("carr-budi-jaya", "Budi Jaya", "Budi-Jaya-Store"),
    "Alif-Store": ("carr-alif", "Alif Store", "Alif-Store"),
}


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store_id: str
    camera_id: str | None = None
    client_id: str = Field(min_length=1, max_length=100)


class Client(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=100)


class Layout(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    store_id: str
    camera_id: str
    x: float = Field(ge=5, le=95)
    y: float = Field(ge=5, le=95)
    heading: float = Field(default=120, ge=0, le=360)
    expected_revision: int = Field(strict=True, ge=0)


def location(video):
    if (
        video.get("collection_session", "").startswith("carr/research/")
        and video["camera_id"] in CARR_LOCATIONS
    ):
        return CARR_LOCATIONS[video["camera_id"]]
    return (
        video["store_id"],
        video["store_id"].replace("-", " ").replace("_", " ").title(),
        video["camera_id"],
    )


class ControlRoom:
    lease_seconds = 60

    def __init__(self, ops):
        self.ops, self.live = ops, ops.live
        self.lock, self.switch_lock = threading.RLock(), threading.RLock()
        self.halt, self.skip, self.closed = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        self.thread = None
        self.clients = {}
        self.config = None
        self.session = None
        self.index = 0
        self.cycle = 1
        self.error = None
        self.phase = "idle"
        self.video = None
        with ops.catalog.connect(write=True) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS ops_camera_layout(store_id TEXT,camera_id TEXT,payload TEXT NOT NULL,PRIMARY KEY(store_id,camera_id))"
            )

    def manifest(self):
        stores = {}
        with self.ops.catalog.connect() as db:
            layouts = {
                (r[0], r[1]): json.loads(r[2])
                for r in db.execute("SELECT * FROM ops_camera_layout")
            }
        with self.ops.catalog.connect() as db:
            profiles = {
                row[0]: json.loads(row[1])["name"]
                for row in db.execute("SELECT * FROM ops_store_profiles")
            }
        for video in self.ops.catalog.videos():
            profile = video["source_profile"]
            if (
                profile.get("simulated")
                or profile["source_kind"] == "sparse_clip"
                or video["frame_dims_px"][0] < 1280
            ):
                continue
            if not Path(video["path"]).is_file():
                continue
            key, name, camera_id = location(video)
            store = stores.setdefault(
                key,
                {
                    "id": key,
                    "name": profiles.get(key, name),
                    "source_location": name,
                    "cameras": {},
                },
            )
            camera = store["cameras"].setdefault(
                camera_id,
                {
                    "id": camera_id,
                    "name": "Camera " + str(len(store["cameras"]) + 1).zfill(2),
                    "clips": [],
                    "layout": layouts.get(
                        (key, camera_id),
                        {
                            "x": 20 + len(store["cameras"]) * 23 % 60,
                            "y": 25 + len(store["cameras"]) * 17 % 50,
                            "heading": 120,
                            "revision": 0,
                        },
                    ),
                },
            )
            camera["clips"].append(
                {
                    "id": video["id"],
                    "name": video["display_name"],
                    "duration_seconds": round(
                        (video.get("declared_duration_ms") or video["indexed_span_ms"])
                        / 1000,
                        2,
                    ),
                    "dimensions": video["frame_dims_px"],
                    "media_url": f"/api/videos/{video['id']}/content",
                    "thumbnail_url": f"/api/videos/{video['id']}/frame/0",
                    "source_kind": profile["source_kind"],
                    "dataset": profile["dataset"],
                    "source_key": (
                        re.search(r"V\d+", video.get("collection_session", "")).group(0)
                        if video.get("collection_session", "").startswith(
                            "carr/research/"
                        )
                        and re.search(r"V\d+", video.get("collection_session", ""))
                        else video["media_sha256"]
                    ),
                }
            )
        result = []
        for store in stores.values():
            store["cameras"] = list(store["cameras"].values())
            for camera in store["cameras"]:
                # Avoid prioritizing a duplicate short excerpt of the installed long clip.
                camera["clips"].sort(key=lambda c: (-c["duration_seconds"], c["id"]))
                unique = {}
                for clip in camera["clips"]:
                    unique.setdefault(clip["source_key"], clip)
                camera["clips"] = list(unique.values())
                camera["seconds"] = round(
                    sum(c["duration_seconds"] for c in camera["clips"]), 2
                )
            store["clip_count"] = sum(len(c["clips"]) for c in store["cameras"])
            store["seconds"] = round(sum(c["seconds"] for c in store["cameras"]), 2)
            result.append(store)
        result.sort(key=lambda s: (-s["clip_count"], -s["seconds"], s["name"]))
        return {
            "stores": result,
            "default_store_id": result[0]["id"] if result else None,
            "basis": "recorded_source_live_analysis",
            "map_basis": "schematic",
            "live_feed_available": False,
        }

    def save_layout(self, value):
        store = next(
            (s for s in self.manifest()["stores"] if s["id"] == value["store_id"]), None
        )
        if not store or not any(
            c["id"] == value["camera_id"] for c in store["cameras"]
        ):
            raise KeyError("Camera not found in this store")
        key = (value["store_id"], value["camera_id"])
        with self.ops.catalog.connect(write=True) as db:
            old = db.execute(
                "SELECT payload FROM ops_camera_layout WHERE store_id=? AND camera_id=?",
                key,
            ).fetchone()
            revision = json.loads(old[0])["revision"] if old else 0
            if revision != value["expected_revision"]:
                raise RuntimeError("Camera layout changed. Refresh and retry.")
            result = {k: value[k] for k in ("x", "y", "heading")} | {
                "revision": revision + 1
            }
            db.execute(
                "INSERT OR REPLACE INTO ops_camera_layout VALUES (?,?,?)",
                key + (canonical(result),),
            )
        return result

    def start(self, value):
        with self.switch_lock:
            store = next(
                (s for s in self.manifest()["stores"] if s["id"] == value["store_id"]),
                None,
            )
            if not store:
                raise KeyError("No HD continuous recordings for this store")
            camera = next(
                (c for c in store["cameras"] if c["id"] == value.get("camera_id")),
                store["cameras"][0] if not value.get("camera_id") else None,
            )
            if not camera:
                raise KeyError("Camera is not part of this store")
            with self.lock:
                same = (
                    self.phase in ("starting", "running")
                    and self.config
                    and (self.config["store_id"], self.config["camera_id"])
                    == (store["id"], camera["id"])
                )
                if same:
                    self.clients[value["client_id"]] = time.monotonic()
                    return self.status()
            self._stop()
            with self.live.lock:
                if any(not s["done"].is_set() for s in self.live.sessions.values()):
                    raise RuntimeError(
                        "Another camera analysis is running. Stop it in Monitor first."
                    )
            with self.lock:
                self.clients = {value["client_id"]: time.monotonic()}
                self.halt = threading.Event()
                self.skip = threading.Event()
                self.config = {
                    "store_id": store["id"],
                    "store_name": store["name"],
                    "camera_id": camera["id"],
                    "camera_name": camera["name"],
                    "clips": camera["clips"],
                }
                self.index = 0
                self.cycle = 1
                self.error = None
                self.phase = "starting"
                self.session = None
                self.video = camera["clips"][0]
                self.thread = threading.Thread(
                    target=self._run, name="store-camera-patrol", daemon=True
                )
                self.thread.start()
            return self.status()

    def heartbeat(self, client):
        with self.lock:
            if client not in self.clients:
                raise KeyError("Monitoring lease expired")
            self.clients[client] = time.monotonic()
        return {"active": self.phase in ("starting", "running")}

    def stop(self, client):
        with self.switch_lock:
            with self.lock:
                self.clients.pop(client, None)
                remaining = bool(self.clients)
            if not remaining:
                self._stop()
        return self.status()

    def next(self, client):
        with self.lock:
            if client not in self.clients:
                raise KeyError("Monitoring lease expired")
            self.skip.set()
        return {"status": "switching"}

    def _stop(self):
        self.halt.set()
        with self.lock:
            key = self.session
            thread = self.thread
        if key:
            try:
                self.live.stop(key)
            except KeyError:
                pass
        if thread and thread is not threading.current_thread():
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError(
                    "The current model operation is still stopping; retry shortly."
                )
        with self.lock:
            self.phase = "paused" if self.config else "idle"
            self.clients = {}

    def _run(self):
        errors = 0
        try:
            while not self.halt.is_set() and not self.closed.is_set():
                with self.lock:
                    clip = self.config["clips"][self.index]
                    self.video = clip
                    self.session = None
                    self.phase = "starting"
                    self.skip.clear()
                saved = self.live.start(clip["id"])
                session = self.live.get(saved["id"])
                (session["folder"] / ".control-room").write_text(
                    "Generated by camera patrol. Reviewed and reported runs are retained.\n"
                )
                with self.lock:
                    self.session = saved["id"]
                while not session["done"].wait(0.15):
                    with self.lock:
                        now = time.monotonic()
                        self.clients = {
                            c: at
                            for c, at in self.clients.items()
                            if now - at < self.lease_seconds
                        }
                        expired = not self.clients
                        self.phase = (
                            "running" if session["status"] == "running" else "starting"
                        )
                    if (
                        self.halt.is_set()
                        or self.closed.is_set()
                        or self.skip.is_set()
                        or expired
                    ):
                        if expired:
                            self.halt.set()
                        self.live.stop(saved["id"])
                if self.halt.is_set() or self.closed.is_set():
                    break
                if session["status"] == "error":
                    errors += 1
                    with self.lock:
                        self.error = session["payload"].get(
                            "error", "Analysis unavailable"
                        )
                    if errors >= len(self.config["clips"]):
                        raise RuntimeError(self.error)
                else:
                    errors = 0
                    with self.lock:
                        self.error = None
                self.prune(clip["id"])
                with self.lock:
                    self.index = (self.index + 1) % len(self.config["clips"])
                    if self.index == 0:
                        self.cycle += 1
        except Exception as error:  # noqa: BLE001 - surface background worker failures to the operator
            with self.lock:
                self.phase = "error"
                self.error = str(error)
        finally:
            with self.lock:
                if self.phase != "error":
                    self.phase = "paused"

    def status(self):
        with self.lock:
            result = {
                "status": self.phase,
                "error": self.error,
                "session_id": self.session,
                "video": self.video,
                "clip_index": self.index,
                "cycle": self.cycle,
                "config": self.config,
            }
        if result["session_id"]:
            session = self.live.get(result["session_id"])
            result["sequence"] = session["sequence"]
            result["latest"] = session["payload"]
            result["geometry"] = load_geometry(self.ops.catalog, session["video_id"])[
                "geometry"
            ]
        return result

    def prune(self, video_id):
        """Bound unattended patrol output, retaining reviewed/report-referenced evidence."""
        owned = []
        for marker in self.live.root.glob("*/.control-room"):
            try:
                summary = json.loads((marker.parent / "summary.json").read_text())
                if summary.get("video_id") == video_id:
                    owned.append(marker.parent)
            except (OSError, ValueError):
                continue
        if len(owned) <= 3:
            return
        protected = set()
        for path in self.ops.root.glob("*/report.json"):
            try:
                protected.update(
                    s["run_key"]
                    for s in json.loads(path.read_text()).get("sources", [])
                )
            except (OSError, ValueError, KeyError):
                continue
        for folder in sorted(owned, key=lambda p: p.stat().st_mtime, reverse=True)[3:]:
            key = "live:" + folder.name
            if key not in protected and not self.ops.reviews(key):
                shutil.rmtree(folder)
                with self.live.lock:
                    self.live.sessions.pop(folder.name, None)

    def shutdown(self):
        self.closed.set()
        self._stop()


def router(room):
    r = APIRouter(prefix="/api/control-room")

    @r.get("/manifest")
    def manifest():
        return room.manifest()

    @r.put("/layout")
    def layout(value: Layout):
        return room.save_layout(value.model_dump())

    @r.post("/start")
    def start(value: Start):
        return room.start(value.model_dump())

    @r.post("/heartbeat")
    def heartbeat(value: Client):
        return room.heartbeat(value.client_id)

    @r.post("/stop")
    def stop(value: Client):
        return room.stop(value.client_id)

    @r.post("/next")
    def next_clip(value: Client):
        return room.next(value.client_id)

    @r.get("/status")
    def status():
        value = room.status()
        value.pop("latest", None)
        return value

    @r.get("/stream")
    def stream():
        def events():
            last = None
            while not room.closed.is_set():
                value = room.status()
                signature = (
                    value["status"],
                    value["session_id"],
                    value.get("sequence"),
                    value["cycle"],
                )
                if signature != last:
                    last = signature
                    yield "data: " + json.dumps(value) + "\n\n"
                else:
                    yield ": keepalive\n\n"
                if room.closed.wait(0.2):
                    return

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return r
