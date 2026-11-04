"""Persistent store work orders with atomic stock completion and shelf check policies."""

import json
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import shelf_operations
from app.storage import canonical

STORE_NAME = "Aster Market"


class StoreProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("Store name cannot be blank")
        return value.strip()


class CheckPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_interval_hours: int = Field(strict=True, ge=1, le=168)
    expected_revision: int = Field(strict=True, ge=0)


class NewTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store_id: str
    title: str = Field(min_length=1, max_length=160)
    assignee: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=1000)
    due_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Enter a task title")
        return value.strip()

    @field_validator("due_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("A due time needs a timezone offset")
        return value.astimezone(UTC) if value else None


class TaskChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern="^(open|in_progress|completed|cancelled)$")
    assignee: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=1000)
    expected_revision: int = Field(strict=True, ge=1)
    quantity: int | None = Field(default=None, strict=True, ge=0, le=1000000)
    expected_stock_revision: int | None = Field(default=None, strict=True, ge=0)


def initialize(catalog):
    with catalog.connect(write=True) as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS ops_store_profiles(store_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ops_shelf_policies(store_id TEXT,camera_id TEXT,shelf_id TEXT,payload TEXT NOT NULL,PRIMARY KEY(store_id,camera_id,shelf_id));
        CREATE TABLE IF NOT EXISTS ops_tasks(id TEXT PRIMARY KEY,store_id TEXT NOT NULL,camera_id TEXT,shelf_id TEXT,kind TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS ops_one_open_shelf_task ON ops_tasks(store_id,camera_id,shelf_id,kind) WHERE status IN ('open','in_progress') AND kind!='manual';""")


def store_name(ops, store_id):
    with ops.catalog.connect() as db:
        row = db.execute(
            "SELECT payload FROM ops_store_profiles WHERE store_id=?", (store_id,)
        ).fetchone()
    if row:
        return json.loads(row[0])["name"]
    return (
        STORE_NAME
        if store_id in ("sim-store", "demo-store", "aster-market")
        else store_id.replace("-", " ")
        .replace("_", " ")
        .title()
        .replace("Demo", "")
        .strip()
    )


def save_store(ops, store_id, value):
    if not any(v["store_id"] == store_id for v in ops.catalog.videos()):
        raise KeyError("Store not found")
    with ops.catalog.connect(write=True) as db:
        db.execute(
            "INSERT OR REPLACE INTO ops_store_profiles VALUES (?,?)",
            (store_id, canonical(value)),
        )
    return value


def policies(ops, store_id):
    with ops.catalog.connect() as db:
        return {
            (r[0], r[1]): json.loads(r[2])
            for r in db.execute(
                "SELECT camera_id,shelf_id,payload FROM ops_shelf_policies WHERE store_id=?",
                (store_id,),
            )
        }


def save_policy(ops, video_id, shelf_id, value):
    video, _ = shelf_operations.identity(ops, video_id, shelf_id)
    key = (video["store_id"], video["camera_id"], shelf_id)
    with ops.lock, ops.catalog.connect(write=True) as db:
        row = db.execute(
            "SELECT payload FROM ops_shelf_policies WHERE store_id=? AND camera_id=? AND shelf_id=?",
            key,
        ).fetchone()
        revision = json.loads(row[0])["revision"] if row else 0
        if value["expected_revision"] != revision:
            raise RuntimeError("Shelf check policy changed. Refresh and retry.")
        result = {
            "check_interval_hours": value["check_interval_hours"],
            "revision": revision + 1,
        }
        db.execute(
            "INSERT OR REPLACE INTO ops_shelf_policies VALUES (?,?,?,?)",
            key + (canonical(result),),
        )
    return result


def save_task(db, task):
    db.execute(
        "INSERT OR REPLACE INTO ops_tasks VALUES (?,?,?,?,?,?,?)",
        (
            task["id"],
            task["store_id"],
            task.get("camera_id"),
            task.get("shelf_id"),
            task["kind"],
            task["status"],
            canonical(task),
        ),
    )


def new_task(store_id, title, kind="manual", **fields):
    now = datetime.now(UTC).isoformat()
    return {
        "id": uuid.uuid4().hex,
        "store_id": store_id,
        "title": title,
        "kind": kind,
        "status": "open",
        "assignee": "",
        "notes": "",
        "due_at": None,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "history": [],
    } | fields


def create(ops, value):
    if not any(v["store_id"] == value["store_id"] for v in ops.catalog.videos()):
        raise KeyError("Store not found")
    task = new_task(
        value["store_id"],
        value["title"],
        **{k: v for k, v in value.items() if k not in ("store_id", "title")},
    )
    with ops.catalog.connect(write=True) as db:
        save_task(db, task)
    return task


def board(ops, store_id=None):
    with ops.lock:
        return _board(ops, store_id)


def _board(ops, store_id=None):
    stores = ops.stores()
    selected = (
        next((s for s in stores if s["id"] == store_id), None)
        if store_id
        else next(iter(stores), None)
    )
    if not selected:
        if store_id:
            raise KeyError("Store not found")
        return {
            "store": None,
            "stores": [],
            "tasks": [],
            "shelves": [],
            "cameras": [],
            "summary": {},
        }
    store_id = selected["id"]
    cameras = {}
    # One current geometry per camera; recordings never multiply stock or work orders.
    for video in ops.catalog.videos():
        if video["store_id"] == store_id and video["camera_id"] not in cameras:
            cameras[video["camera_id"]] = video
    configured = policies(ops, store_id)
    shelves = []
    for video in cameras.values():
        snapshot = shelf_operations.snapshot(ops, video["id"], metrics=False)
        for shelf in snapshot["shelves"]:
            policy = configured.get(
                (video["camera_id"], shelf["id"]),
                {"check_interval_hours": 4, "revision": 0},
            )
            stock = shelf["stock"]
            due = (
                datetime.fromisoformat(stock["updated_at"])
                + timedelta(hours=policy["check_interval_hours"])
                if stock
                else datetime.now(UTC)
            )
            shelves.append(
                shelf
                | {
                    "video_id": video["id"],
                    "camera_id": video["camera_id"],
                    "policy": policy,
                    "check_due_at": due.isoformat(),
                }
            )
    now = datetime.now(UTC)
    with ops.lock, ops.catalog.connect(write=True) as db:
        active = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM ops_tasks WHERE store_id=? AND status IN ('open','in_progress')",
                (store_id,),
            )
        ]
        current_shelves = {(s["camera_id"], s["id"]) for s in shelves}
        for task in active:
            if (
                task["kind"] != "manual"
                and (task.get("camera_id"), task.get("shelf_id")) not in current_shelves
            ):
                task.update(
                    status="cancelled",
                    revision=task["revision"] + 1,
                    updated_at=now.isoformat(),
                )
                task["history"].append(
                    {
                        "at": now.isoformat(),
                        "status": "cancelled",
                        "notes": "Shelf removed from the current camera geometry.",
                    }
                )
                save_task(db, task)
        active = [t for t in active if t["status"] in ("open", "in_progress")]
        for shelf in shelves:
            for kind, needed in (
                ("refill", shelf["status"] in ("empty", "refill")),
                (
                    "audit",
                    shelf["stock"] is None
                    or datetime.fromisoformat(shelf["check_due_at"]) <= now,
                ),
            ):
                existing = next(
                    (
                        t
                        for t in active
                        if t.get("camera_id") == shelf["camera_id"]
                        and t.get("shelf_id") == shelf["id"]
                        and t["kind"] == kind
                    ),
                    None,
                )
                if existing and (
                    existing.get("video_id") != shelf["video_id"]
                    or existing["title"] != shelf["name"]
                ):
                    existing.update(
                        video_id=shelf["video_id"],
                        title=shelf["name"],
                        revision=existing["revision"] + 1,
                        updated_at=now.isoformat(),
                    )
                    save_task(db, existing)
                if needed and existing is None:
                    task = new_task(
                        store_id,
                        shelf["name"],
                        kind,
                        video_id=shelf["video_id"],
                        camera_id=shelf["camera_id"],
                        shelf_id=shelf["id"],
                        simulated=selected["simulated"],
                        due_at=now.isoformat()
                        if kind == "refill"
                        else shelf["check_due_at"],
                    )
                    save_task(db, task)
                elif not needed and existing:
                    rescheduled = kind == "audit" and datetime.fromisoformat(
                        shelf["stock"]["updated_at"]
                    ) <= datetime.fromisoformat(existing["created_at"])
                    resolved_status = "cancelled" if rescheduled else "completed"
                    existing.update(
                        status=resolved_status,
                        revision=existing["revision"] + 1,
                        updated_at=now.isoformat(),
                    )
                    existing["history"].append(
                        {
                            "at": now.isoformat(),
                            "status": resolved_status,
                            "notes": "Check rescheduled by policy."
                            if rescheduled
                            else "Resolved by confirmed stock update.",
                        }
                    )
                    save_task(db, existing)
        tasks = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM ops_tasks WHERE store_id=? ORDER BY rowid DESC",
                (store_id,),
            )
        ]
    for task in tasks:
        task["overdue"] = (
            task["status"] in ("open", "in_progress")
            and bool(task.get("due_at"))
            and datetime.fromisoformat(task["due_at"]) < now
        )
    tasks.sort(
        key=lambda t: (
            t["status"] in ("completed", "cancelled"),
            not t["overdue"],
            t.get("due_at") or "9999",
            t["created_at"],
        )
    )
    return {
        "store": selected,
        "stores": stores,
        "tasks": tasks,
        "shelves": shelves,
        "cameras": [
            {
                "camera_id": v["camera_id"],
                "video_id": v["id"],
                "name": v["display_name"],
                "simulated": v["source_profile"].get("simulated", False),
            }
            for v in cameras.values()
        ],
        "summary": {
            "open": sum(t["status"] in ("open", "in_progress") for t in tasks),
            "overdue": sum(t["overdue"] for t in tasks),
            "completed": sum(t["status"] == "completed" for t in tasks),
            "refill": sum(s["status"] in ("empty", "refill") for s in shelves),
        },
        "as_of": now.isoformat(),
    }


def change(ops, task_id, value):
    with ops.lock, ops.catalog.connect(write=True) as db:
        row = db.execute(
            "SELECT payload FROM ops_tasks WHERE id=?", (task_id,)
        ).fetchone()
        if not row:
            raise KeyError("Work order not found")
        task = json.loads(row[0])
        if value["expected_revision"] != task["revision"]:
            raise RuntimeError("Work order changed. Refresh and retry.")
        if task["status"] in ("completed", "cancelled"):
            raise ValueError("Closed work orders are read-only. Create a new task.")
        if task["kind"] != "manual" and value["status"] == "cancelled":
            raise ValueError(
                "Resolve the shelf condition or change its check interval to close this task."
            )
        if value["status"] == "completed" and task["kind"] in ("refill", "audit"):
            if value["quantity"] is None or value["expected_stock_revision"] is None:
                raise ValueError(
                    "Completion needs a confirmed quantity and the current stock revision"
                )
            if task["kind"] == "refill" and value["quantity"] == 0:
                raise ValueError("Confirm at least one refill unit")
            stock_row = db.execute(
                "SELECT payload FROM ops_shelf_stock WHERE store_id=? AND camera_id=? AND shelf_id=?",
                (task["store_id"], task["camera_id"], task["shelf_id"]),
            ).fetchone()
            stock = (
                json.loads(stock_row[0])
                if stock_row
                else {"capacity": 60, "reorder_at": 12}
            )
            result = shelf_operations.update(
                ops,
                task["video_id"],
                task["shelf_id"],
                {
                    "action": "refill" if task["kind"] == "refill" else "count",
                    "quantity": value["quantity"],
                    "capacity": stock["capacity"],
                    "reorder_at": stock["reorder_at"],
                    "expected_revision": value["expected_stock_revision"],
                    "notes": value["notes"] or "Completed work order " + task_id,
                },
                connection=db,
            )
            if task["kind"] == "refill" and result["on_hand"] <= result["reorder_at"]:
                raise ValueError(
                    "Add enough units to clear the refill threshold before completing"
                )
            task["stock_revision"] = result["revision"]
        now = datetime.now(UTC).isoformat()
        task.update(
            status=value["status"],
            assignee=value["assignee"].strip(),
            notes=value["notes"],
            revision=task["revision"] + 1,
            updated_at=now,
        )
        task["history"].append(
            {
                "at": now,
                "status": task["status"],
                "assignee": task["assignee"],
                "notes": value["notes"],
            }
        )
        save_task(db, task)
    return task


def report_snapshot(ops, items):
    result = []
    ids = {r["video_id"] for r in items}
    for store_id in sorted({r["store_id"] for r in items}):
        current = board(ops, store_id)
        result.append(
            {
                "store_id": store_id,
                "name": current["store"]["name"],
                "as_of": current["as_of"],
                "simulated": current["store"]["simulated"],
                "tasks": [
                    t
                    for t in current["tasks"]
                    if t["kind"] == "manual" or t.get("video_id") in ids
                ],
            }
        )
    return result
