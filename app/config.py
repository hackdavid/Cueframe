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
    plan_reasoning: str = "medium"  # planning effort; "" leaves it to the model
    research_confidence: float = 0.7
    research_max_pages: int = 4
    research_max_rounds: int = 2
    sandbox_provider: str = "modal"
    modal_profile: str | None = None
    modal_app: str = "cueframe-sandbox"
    sandbox_cpu: float = 2
    sandbox_memory_mb: int = 4096
    sandbox_max_seconds: int = 900
    sandbox_idle_seconds: int = 120
    sandbox_width: int = 1280
    sandbox_height: int = 720
    sandbox_scale: float = 1
    sandbox_term_font: int = 12
    sandbox_apt_extra: tuple[str, ...] = ()
    sandbox_usd_per_hour: float = 0.11
    sandbox_max_active: int = 4  # across all users: caps what can be billed at once
    user_max_sandboxes: int = 2  # running at once per user
    user_daily_sandbox_minutes: float = 60  # sandbox time per user per rolling 24 h
    ask_timeout_seconds: float = 900
    r2_endpoint: str | None = None
    r2_key_id: str | None = None
    r2_secret: str | None = None
    r2_bucket: str | None = None
    r2_prefix: str = "cueframe"

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
        plan_reasoning=os.environ.get("CUEFRAME_PLAN_REASONING", "medium"),
        research_confidence=_float("CUEFRAME_RESEARCH_CONFIDENCE", 0.7),
        research_max_pages=_int("CUEFRAME_RESEARCH_MAX_PAGES", 4),
        research_max_rounds=_int("CUEFRAME_RESEARCH_MAX_ROUNDS", 2),
        sandbox_provider=os.environ.get("CUEFRAME_SANDBOX_PROVIDER", "modal"),
        modal_profile=os.environ.get("CUEFRAME_MODAL_PROFILE") or None,
        modal_app=os.environ.get("CUEFRAME_MODAL_APP", "cueframe-sandbox"),
        sandbox_cpu=_float("CUEFRAME_SANDBOX_CPU", 2),
        sandbox_memory_mb=_int("CUEFRAME_SANDBOX_MEMORY_MB", 4096),
        sandbox_max_seconds=_int("CUEFRAME_SANDBOX_MAX_SECONDS", 900),
        sandbox_idle_seconds=_int("CUEFRAME_SANDBOX_IDLE_SECONDS", 120),
        sandbox_width=_int("CUEFRAME_SANDBOX_WIDTH", 1280),
        sandbox_height=_int("CUEFRAME_SANDBOX_HEIGHT", 720),
        sandbox_scale=_float("CUEFRAME_SANDBOX_SCALE", 1),
        sandbox_term_font=_int("CUEFRAME_SANDBOX_TERM_FONT", 12),
        sandbox_apt_extra=tuple(os.environ.get("CUEFRAME_SANDBOX_APT_EXTRA", "").split()),
        sandbox_usd_per_hour=_float("CUEFRAME_SANDBOX_USD_PER_HOUR", 0.11),
        sandbox_max_active=_int("CUEFRAME_SANDBOX_MAX_ACTIVE", 4),
        user_max_sandboxes=_int("CUEFRAME_USER_MAX_SANDBOXES", 2),
        user_daily_sandbox_minutes=_float("CUEFRAME_USER_DAILY_SANDBOX_MINUTES", 60),
        ask_timeout_seconds=_float("CUEFRAME_ASK_TIMEOUT_SECONDS", 900),
        r2_endpoint=os.environ.get("R2_ENDPOINT_URL") or None,
        r2_key_id=os.environ.get("R2_ACCESS_KEY_ID") or None,
        r2_secret=os.environ.get("R2_SECRET_ACCESS_KEY") or None,
        r2_bucket=os.environ.get("R2_BUCKET_NAME") or None,
        r2_prefix=os.environ.get("CUEFRAME_R2_PREFIX", "cueframe"),
    )
