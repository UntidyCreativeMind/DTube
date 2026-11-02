import os
import tempfile
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="dtube-tests-"))
os.environ["DTUBE_CONFIG"] = str(ROOT / "config")
os.environ["DTUBE_MEDIA"] = str(ROOT / "media")
(ROOT / "media").mkdir()

import pytest

from dtube import db


@pytest.fixture(autouse=True)
def fresh_db():
    (db.CONFIG_DIR).mkdir(exist_ok=True)
    for name in ("dtube.db", "dtube.db-wal", "dtube.db-shm"):
        (db.CONFIG_DIR / name).unlink(missing_ok=True)
    db.init()
    yield
