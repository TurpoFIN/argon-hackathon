import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from backend import prep_viewer


class PreparationViewerTests(unittest.TestCase):
    def test_viewer_can_probe_layers_with_head_before_getting_them(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = root / "storm" / "argon"
            model.mkdir(parents=True)
            (model / "control_layers.json").write_text('{"flood":[]}')
            app = FastAPI()
            app.include_router(prep_viewer.router)
            with patch.object(prep_viewer, "RUNS", root), TestClient(app) as client:
                url = "/runs/storm/argon/control_layers.json"
                self.assertEqual(client.head(url).status_code, 200)
                self.assertEqual(client.get(url).json(), {"flood": []})

    def test_only_complete_comparisons_are_offered(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = root / "storm" / "argon"
            model.mkdir(parents=True)
            (model / "argon.json").write_text(json.dumps({"rounds": [
                {"round": 1, "ledger": {"total_usd": 10}},
                {"round": 2, "ledger": {"total_usd": 8}},
            ]}))
            (model / "argon_round1_layers.json").write_text("{}")
            self.assertEqual(prep_viewer.available_runs(root), [])
            (model / "control_layers.json").write_text("{}")
            found = prep_viewer.available_runs(root)
            self.assertEqual([(r["run"], r["model"], r["rounds"]) for r in found],
                             [("storm", "argon", [1])])

    def test_run_files_cannot_escape_the_data_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "runs"
            model = root / "storm" / "argon"
            model.mkdir(parents=True)
            outside = Path(folder) / "outside.json"
            outside.write_text("{}")
            (model / "escape.json").symlink_to(outside)
            with patch.object(prep_viewer, "RUNS", root):
                for args in [("..", "argon", "outside.json"),
                             ("storm", "argon", "../outside.json"),
                             ("storm", "argon", "escape.json")]:
                    with self.assertRaises(HTTPException):
                        prep_viewer.preparation_result(*args)


if __name__ == "__main__":
    unittest.main()
