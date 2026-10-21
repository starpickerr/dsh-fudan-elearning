"""Build this independent Server; no Stella business is bundled by Core."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--name",
            "elearning-mcp",
            "--distpath",
            str(args.dist_dir.resolve()),
            "--workpath",
            str(root / "build/work"),
            "--specpath",
            str(root / "build"),
            "--paths",
            str(root),
            "--collect-submodules",
            "mcp",
            "--collect-data",
            "elearning_mcp",
            "--exclude-module",
            "pytest",
            "--exclude-module",
            "numpy",
            "--exclude-module",
            "torch",
            str(root / "mcp_server.py"),
        ],
        check=True,
        cwd=root,
        env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(root / "build/cache")},
    )
