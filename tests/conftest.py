import os
import tempfile

os.environ.setdefault("CUEFRAME_DATA_DIR", tempfile.mkdtemp(prefix="cueframe-import-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        max_upload_mb=1,
        max_concurrent_jobs=2,
        demo_job_seconds=0.2,
        demo_render_video=False,
        admin_username="admin",
        admin_password="pw-test",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        res = c.post("/api/auth/login", json={"username": "admin", "password": "pw-test"})
        assert res.status_code == 200
        c.token = res.json()["token"]
        yield c


@pytest.fixture
def anon(settings):
    with TestClient(create_app(settings)) as c:
        yield c
