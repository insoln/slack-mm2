"""Utility to (re)generate slack-mini-backup.zip without external zip binary.

Run: python build_mini_backup_zip.py (from this directory or project root)
"""

from pathlib import Path
import zipfile

HERE = Path(__file__).parent
SRC_DIR = HERE / "slack-mini-backup"
ZIP_PATH = HERE / "slack-mini-backup.zip"


def build():
    if not SRC_DIR.exists():
        raise SystemExit(f"Source directory missing: {SRC_DIR}")
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in SRC_DIR.rglob("*"):
            if path.is_file():
                arcname = path.relative_to(SRC_DIR.parent)
                zf.write(path, arcname)
    print(f"Created {ZIP_PATH} ({ZIP_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
