import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.replay import main


class ReplayGateTests(unittest.TestCase):
    def test_missing_contract_does_not_create_database_or_consume_input(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "must-not-exist.sqlite"
            output = io.StringIO()
            with patch("app.replay.missing_p0_inputs", return_value=[Path("contracts/v1/messages.schema.json")]):
                with contextlib.redirect_stdout(output):
                    code = main(["--input", "not-present.jsonl", "--config", "not-present.json", "--db", str(db)])
            self.assertEqual(code, 2)
            self.assertFalse(db.exists())
            self.assertIn("messages.schema.json", output.getvalue())

    def test_available_contract_does_not_accept_nonexistent_input_or_create_database(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "unused.sqlite"
            with patch("app.replay.missing_p0_inputs", return_value=[]):
                with contextlib.redirect_stderr(output):
                    code = main(["--input", "unknown.jsonl", "--config", "unknown.json", "--db", str(db)])
            self.assertEqual(code, 2)
            self.assertIn("Replay rejected", output.getvalue())
            self.assertFalse(db.exists())
