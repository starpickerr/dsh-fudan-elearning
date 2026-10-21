"""Concurrent independent MCP connections must not lose checkpoints."""

import os
from pathlib import Path
import subprocess
import sys


def test_two_processes_preserve_all_cursor_updates(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = """
import json,sys,time
from elearning_mcp.store import ElearningStore
store=ElearningStore(sys.argv[1]); key=sys.argv[2]
original=json.loads
def slow_loads(*args,**kwargs):
    value=original(*args,**kwargs);time.sleep(.005);return value
json.loads=slow_loads
for index in range(20):store.set_cursors({key+str(index):str(index)})
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path / "state"), key],
            env={**os.environ, "PYTHONPATH": str(root)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for key in ["a", "b"]
    ]
    for process in processes:
        _, error = process.communicate(timeout=30)
        assert process.returncode == 0, error.decode()
    from elearning_mcp.store import ElearningStore

    assert len(ElearningStore(tmp_path / "state").get_cursors()) == 40
