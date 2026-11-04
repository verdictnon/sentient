"""Work completion and inventory must commit together, without duplicate tasks."""

import json
import unittest
from datetime import UTC, datetime, timedelta

import test_shelf_operations as fixtures


class WorkOrderTests(unittest.TestCase):
    setUp = fixtures.ShelfOperationsTests.setUp
    tearDown = fixtures.ShelfOperationsTests.tearDown
    update = fixtures.ShelfOperationsTests.update
    save_run = fixtures.ShelfOperationsTests.save_run

    def board(self):
        response = self.client.get("/api/ops/work-orders", params={"store_id": "shop"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def complete(self, task, quantity, revision):
        return self.client.put(
            "/api/ops/work-orders/" + task["id"],
            json={
                "status": "completed",
                "expected_revision": task["revision"],
                "assignee": "Dana",
                "notes": "Confirmed on the floor",
                "quantity": quantity,
                "expected_stock_revision": revision,
            },
        )

    def test_initial_audit_and_refill_generation_are_idempotent(self):
        a = self.board()
        b = self.board()
        self.assertEqual([t["id"] for t in a["tasks"]], [t["id"] for t in b["tasks"]])
        self.assertEqual(a["tasks"][0]["kind"], "audit")
        self.assertEqual(self.complete(a["tasks"][0], 5, 0).status_code, 200)
        tasks = self.board()["tasks"]
        active = [t for t in tasks if t["status"] == "open"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["kind"], "refill")
        self.assertEqual(
            self.client.get(self.base).json()["shelves"][0]["stock"]["on_hand"], 5
        )

    def test_completion_is_atomic_and_stale_edits_are_rejected(self):
        self.update()
        task = next(t for t in self.board()["tasks"] if t["kind"] == "refill")
        # Insufficient replenishment rolls back both the stock ledger and the task.
        self.assertEqual(self.complete(task, 1, 1).status_code, 422)
        self.assertEqual(
            self.client.get(self.base).json()["shelves"][0]["stock"]["revision"], 1
        )
        self.assertEqual(self.board()["tasks"][0]["status"], "open")
        self.assertEqual(self.complete(task, 55, 0).status_code, 409)
        result = self.complete(task, 55, 1)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(
            self.client.get(self.base).json()["shelves"][0]["stock"]["on_hand"], 60
        )
        self.assertEqual(self.complete(task, 55, 1).status_code, 409)
        self.assertEqual(self.board()["summary"]["open"], 0)
        self.assertTrue(result.json()["history"])

    def test_audit_policy_due_times_and_external_count_resolution(self):
        self.update(quantity=30)
        with self.ops.catalog.connect(write=True) as db:
            stock = json.loads(
                db.execute("SELECT payload FROM ops_shelf_stock").fetchone()[0]
            )
            stock["updated_at"] = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
            db.execute("UPDATE ops_shelf_stock SET payload=?", (json.dumps(stock),))
        audit = self.board()["tasks"][0]
        self.assertEqual(audit["kind"], "audit")
        self.assertTrue(audit["overdue"])
        self.assertEqual(
            self.client.put(
                self.base + "/dairy/policy",
                json={"check_interval_hours": 8, "expected_revision": 0},
            ).status_code,
            200,
        )
        self.assertEqual(self.board()["summary"]["open"], 0)
        self.assertEqual(
            self.client.put(
                self.base + "/dairy/policy",
                json={"check_interval_hours": 1, "expected_revision": 0},
            ).status_code,
            409,
        )
        self.client.put(
            self.base + "/dairy/policy",
            json={"check_interval_hours": 1, "expected_revision": 1},
        )
        self.assertEqual(self.board()["summary"]["open"], 1)
        self.update(quantity=30, expected_revision=1)
        self.assertEqual(self.board()["summary"]["open"], 0)

    def test_manual_tasks_assignment_due_date_validation_and_closed_history(self):
        invalid = self.client.post(
            "/api/ops/work-orders",
            json={
                "store_id": "shop",
                "title": "Clean checkout",
                "due_at": "2026-11-03T10:00:00",
            },
        )
        self.assertEqual(invalid.status_code, 422)
        created = self.client.post(
            "/api/ops/work-orders",
            json={
                "store_id": "shop",
                "title": "Clean checkout",
                "due_at": "2026-11-03T10:00:00+05:00",
            },
        ).json()
        self.assertIn("id", created)
        path = "/api/ops/work-orders/" + created["id"]
        assigned = self.client.put(
            path,
            json={
                "status": "in_progress",
                "expected_revision": 1,
                "assignee": "Dana",
                "notes": "After rush hour",
            },
        )
        self.assertEqual(assigned.status_code, 200)
        closed = self.client.put(
            path,
            json={
                "status": "completed",
                "expected_revision": 2,
                "assignee": "Dana",
                "notes": "Cleaned",
            },
        )
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(len(closed.json()["history"]), 2)
        self.assertEqual(
            self.client.put(
                path, json={"status": "open", "expected_revision": 3}
            ).status_code,
            422,
        )

    def test_store_alias_and_removed_shelf_preserve_history(self):
        task = self.board()["tasks"][0]
        r = self.client.put(
            "/api/ops/stores/shop/profile", json={"name": "Aster Market Riverside"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.board()["store"]["name"], "Aster Market Riverside")
        self.assertEqual(self.ops.catalog.video(self.video_id)["store_id"], "shop")
        self.geometry["shapes"] = []
        self.client.put(f"/api/videos/{self.video_id}/geometry", json=self.geometry)
        board = self.board()
        self.assertEqual(board["tasks"][0]["id"], task["id"])
        self.assertEqual(board["tasks"][0]["status"], "cancelled")
        self.assertTrue(board["tasks"][0]["history"])

    def test_report_freezes_current_work_separately_from_activity_period(self):
        self.save_run()
        self.client.post(
            "/api/ops/work-orders",
            json={"store_id": "shop", "title": "Clean checkout", "assignee": "Dana"},
        )
        report = self.client.post(
            "/api/ops/reports",
            json={
                "title": "Store operations",
                "scope": "collection",
                "video_ids": [self.video_id],
            },
        ).json()
        self.assertIn("id", report)
        snapshot = self.client.get("/api/ops/reports/" + report["id"] + "/json").json()[
            "work_orders"
        ][0]
        self.assertEqual(snapshot["name"], "Shop")
        self.assertIn("Clean checkout", [t["title"] for t in snapshot["tasks"]])
        self.assertTrue(snapshot["as_of"])


if __name__ == "__main__":
    unittest.main()
