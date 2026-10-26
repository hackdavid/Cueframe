import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_upload_mb: int
    max_concurrent_jobs: int
    demo_job_seconds: float
    demo_render_video: bool
    admin_username: str = "admin"
    admin_password: str = "cueframe"
    cookie_secure: bool = False
    meta_api_key: str | None = None
    meta_base_url: str = "https://api.meta.ai/v1"
    research_model: str = "muse-spark-1.3-contributor"
    research_confidence: float = 0.7
    research_max_pages: int = 4
    research_max_rounds: int = 2

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cueframe.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("CUEFRAME_DATA_DIR", "data")).resolve(),
        max_upload_mb=_int("CUEFRAME_MAX_UPLOAD_MB", 500),
        max_concurrent_jobs=_int("CUEFRAME_MAX_CONCURRENT_JOBS", 2),
        demo_job_seconds=_float("CUEFRAME_DEMO_JOB_SECONDS", 12),
        demo_render_video=os.environ.get("CUEFRAME_DEMO_RENDER_VIDEO", "1") != "0",
        admin_username=os.environ.get("CUEFRAME_ADMIN_USERNAME", "admin"),
        admin_password=os.environ.get("CUEFRAME_ADMIN_PASSWORD", "cueframe"),
        cookie_secure=os.environ.get("CUEFRAME_COOKIE_SECURE", "0") == "1",
        meta_api_key=os.environ.get("MODEL_API_KEY") or None,
        meta_base_url=os.environ.get("META_BASE_URL", "https://api.meta.ai/v1"),
        research_model=os.environ.get("CUEFRAME_RESEARCH_MODEL", "muse-spark-1.3-contributor"),
        research_confidence=_float("CUEFRAME_RESEARCH_CONFIDENCE", 0.7),
        research_max_pages=_int("CUEFRAME_RESEARCH_MAX_PAGES", 4),
        research_max_rounds=_int("CUEFRAME_RESEARCH_MAX_ROUNDS", 2),
    )
