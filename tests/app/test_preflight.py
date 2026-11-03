"""Bootstrap checks only; these do not certify P0 analytics acceptance."""

import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from app.__main__ import main
from app.preflight import REQUIRED_DOCUMENTS, REQUIRED_P0_INPUTS, missing_documents


class PreflightTests(unittest.TestCase):
    def test_empty_checkout_lists_all_required_documents_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(missing_documents(Path(directory)), REQUIRED_DOCUMENTS)

    def test_directory_cannot_substitute_for_a_document(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "TEAM_PLAN.md").mkdir()
            self.assertIn(Path("TEAM_PLAN.md"), missing_documents(root))

    def test_partial_checkout_reports_only_missing_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "TEAM_PLAN.md").write_text("Presence test only.", encoding="utf-8")
            self.assertEqual(missing_documents(root), REQUIRED_DOCUMENTS[1:])

    def test_missing_inputs_return_nonzero_and_explicit_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["check-inputs", "--root", directory])
            self.assertEqual(code, 2)
            for path in REQUIRED_P0_INPUTS:
                self.assertIn(path.as_posix(), output.getvalue())
            self.assertIn("Replay is unavailable", output.getvalue())

    def test_presence_does_not_claim_acceptance_or_validate_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in REQUIRED_P0_INPUTS:
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("Presence test only.", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["check-inputs", "--root", directory])
            self.assertEqual(code, 0)
            self.assertIn("not contents, fixtures, or P0 acceptance", output.getvalue())

    def test_module_command_reports_missing_inputs(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-m", "app", "check-inputs", "--root", directory],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("P0 blocked", result.stdout)
            self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
