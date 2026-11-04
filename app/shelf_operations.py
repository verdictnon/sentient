"""Operator-confirmed shelf stock, separate from vision pickup candidates."""

import json
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.p1.geometry import load_geometry
from app.storage import canonical


class StockUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(count|refill|remove)$")
    quantity: int = Field(strict=True, ge=0, le=1000000)
    capacity: int = Field(strict=True, ge=1, le=1000000)
    reorder_at: int = Field(strict=True, ge=0, le=1000000)
    expected_revision: int = Field(strict=True, ge=0)
    notes: str = Field(default="", max_length=500)


def initialize(catalog):
    with catalog.connect(write=True) as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS ops_shelf_stock(
            store_id TEXT, camera_id TEXT, shelf_id TEXT, payload TEXT NOT NULL,
            PRIMARY KEY(store_id,camera_id,shelf_id));
            CREATE TABLE IF NOT EXISTS ops_shelf_stock_events(
            store_id TEXT, camera_id TEXT, shelf_id TEXT, revision INTEGER, payload TEXT NOT NULL,
            PRIMARY KEY(store_id,camera_id,shelf_id,revision));""")


def identity(ops, video_id, shelf_id=None):
    video = ops.catalog.video(video_id)
    shapes = load_geometry(ops.catalog, video_id)["geometry"]["shapes"]
    shelves = [s for s in shapes if s["kind"] == "shelf"]
    if shelf_id is not None and not any(s["id"] == shelf_id for s in shelves):
        raise KeyError("Shelf is not present in this camera geometry")
    return video, shelves


def snapshot(ops, video_id, metrics=True):
    video, shelves = identity(ops, video_id)
    key = (video["store_id"], video["camera_id"])
    with ops.catalog.connect() as db:
        stocks = {
            r[0]: json.loads(r[1])
            for r in db.execute(
                "SELECT shelf_id,payload FROM ops_shelf_stock WHERE store_id=? AND camera_id=?",
                key,
            )
        }
        history = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM ops_shelf_stock_events WHERE store_id=? AND camera_id=? ORDER BY rowid DESC LIMIT 50",
                key,
            )
        ]
    areas, run_key = {}, None
    if metrics:
        candidates = ops.runs(video_id)
        if candidates:
            run, _ = ops.best_run(candidates)
            run_key = run["key"]
            areas = {a["id"]: a for a in ops.insight(run_key)[0]["areas"]}
    rows = []
    for shelf in shelves:
        stock = stocks.get(shelf["id"])
        area = areas.get(shelf["id"], {})
        rows.append(
            {
                "id": shelf["id"],
                "name": shelf["name"],
                "product_type": shelf.get("product_type", ""),
                "stock": stock,
                "status": "unconfigured"
                if stock is None
                else "empty"
                if stock["on_hand"] == 0
                else "refill"
                if stock["on_hand"] <= stock["reorder_at"]
                else "healthy",
                "refill_units": None
                if stock is None
                else stock["capacity"] - stock["on_hand"],
                "unique_tracks": area.get("unique_tracks"),
                "visits": area.get("visits"),
                "observed_dwell_seconds": area.get("observed_dwell_seconds"),
            }
        )
    return {
        "video_id": video_id,
        "store_id": key[0],
        "camera_id": key[1],
        "run_key": run_key,
        "simulated": video["source_profile"].get("simulated", False),
        "shelves": rows,
        "history": history,
        "as_of": datetime.now(UTC).isoformat(),
        "basis": "Operator-confirmed stock counts. Pickup candidates never decrement stock. Activity uses one completed run for this recording.",
    }


def update(ops, video_id, shelf_id, value):
    video, shelves = identity(ops, video_id, shelf_id)
    shelf = next(s for s in shelves if s["id"] == shelf_id)
    if value["reorder_at"] >= value["capacity"]:
        raise ValueError("Refill threshold must be below shelf capacity")
    key = (video["store_id"], video["camera_id"], shelf_id)
    with ops.lock, ops.catalog.connect(write=True) as db:
        row = db.execute(
            "SELECT payload FROM ops_shelf_stock WHERE store_id=? AND camera_id=? AND shelf_id=?",
            key,
        ).fetchone()
        old = json.loads(row[0]) if row else None
        if value["expected_revision"] != (old["revision"] if old else 0):
            raise RuntimeError("Stock changed in another view. Refresh before saving.")
        if old is None and value["action"] != "count":
            raise ValueError("Record a shelf count before adding or removing units")
        quantity = value["quantity"]
        on_hand = (
            quantity
            if value["action"] == "count"
            else old["on_hand"]
            + (quantity if value["action"] == "refill" else -quantity)
        )
        if not 0 <= on_hand <= value["capacity"]:
            raise ValueError("Stock must remain between zero and shelf capacity")
        moment = datetime.now(UTC).isoformat()
        result = {
            "on_hand": on_hand,
            "capacity": value["capacity"],
            "reorder_at": value["reorder_at"],
            "revision": value["expected_revision"] + 1,
            "updated_at": moment,
        }
        event = result | {
            "shelf_id": shelf_id,
            "name": shelf["name"],
            "product_type": shelf.get("product_type", ""),
            "action": value["action"],
            "quantity": quantity,
            "before": old["on_hand"] if old else None,
            "notes": value["notes"],
            "simulated": video["source_profile"].get("simulated", False),
        }
        db.execute(
            "INSERT OR REPLACE INTO ops_shelf_stock VALUES (?,?,?,?)",
            key + (canonical(result),),
        )
        db.execute(
            "INSERT INTO ops_shelf_stock_events VALUES (?,?,?,?,?)",
            key + (result["revision"], canonical(event)),
        )
    return result
