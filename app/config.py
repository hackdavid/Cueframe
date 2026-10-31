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
    fix_reasoning: str = "low"  # step repairs and narration rewrites: small, local edits
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
    sandbox_image: str = "desktop"  # "board": headless math-board renderer, no desktop
    board_runner: str = "sandbox"  # "local": render math boards on this server instead
    board_cpu: float = 8
    render_runner: str = "local"  # "modal": render + stitch tutorials in a headless sandbox
    render_cpu: float = 8
    render_memory_mb: int = 8192
    render_max_seconds: int = 2400
    live_view: bool = False  # show the sandbox's live desktop in the chat
    contact_email: str = "hello@cueframe.ai"
    default_plan: str = "free"  # plan of new accounts
    admin_plan: str = "unlimited"  # plan of the CUEFRAME_ADMIN_USERNAME account
    plan_limits: str = ""  # JSON overrides of app.plans.LIMITS
    log_retention_days: int = 30
    public_url: str = "http://localhost:8000"  # share links, QR codes
    open_signup: bool = True
    turnstile_site_key: str = ""  # Cloudflare Turnstile captcha on sign-up (off when unset)
    turnstile_secret: str = ""
    rate_login_per_min: int = 10  # per IP; 0 turns a limit off
    rate_signup_per_hour: int = 5  # per IP
    rate_messages_per_min: int = 20  # per account
    daily_spend_cap_usd: float = 25.0  # estimated spend across all users per UTC day
    video_costs: str = ""  # JSON overrides of app.usage.COST_USD
    alert_webhook: str = ""  # Slack/Discord incoming webhook
    sentry_dsn: str = ""
    watermark_plans: tuple[str, ...] = ("free",)
    referral_bonus: int = 3  # videos a referrer gets when a friend's first video is made
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
        fix_reasoning=os.environ.get("CUEFRAME_FIX_REASONING", "low"),
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
        board_runner=os.environ.get("CUEFRAME_BOARD_RUNNER", "sandbox"),
        render_runner=os.environ.get("CUEFRAME_RENDER_RUNNER", "local"),
        render_cpu=_float("CUEFRAME_RENDER_CPU", 8),
        render_memory_mb=_int("CUEFRAME_RENDER_MEMORY_MB", 8192),
        render_max_seconds=_int("CUEFRAME_RENDER_MAX_SECONDS", 2400),
        live_view=os.environ.get("CUEFRAME_LIVE_VIEW", "0") == "1",
        contact_email=os.environ.get("CUEFRAME_CONTACT_EMAIL", "hello@cueframe.ai"),
        default_plan=os.environ.get("CUEFRAME_DEFAULT_PLAN", "free"),
        admin_plan=os.environ.get("CUEFRAME_ADMIN_PLAN", "unlimited"),
        plan_limits=os.environ.get("CUEFRAME_PLAN_LIMITS", ""),
        log_retention_days=_int("CUEFRAME_LOG_RETENTION_DAYS", 30),
        public_url=os.environ.get("CUEFRAME_PUBLIC_URL", "http://localhost:8000"),
        open_signup=os.environ.get("CUEFRAME_OPEN_SIGNUP", "1").lower() not in ("0", "false"),
        turnstile_site_key=os.environ.get("CUEFRAME_TURNSTILE_SITE_KEY", ""),
        turnstile_secret=os.environ.get("CUEFRAME_TURNSTILE_SECRET", ""),
        rate_login_per_min=_int("CUEFRAME_RATE_LOGIN_PER_MIN", 10),
        rate_signup_per_hour=_int("CUEFRAME_RATE_SIGNUP_PER_HOUR", 5),
        rate_messages_per_min=_int("CUEFRAME_RATE_MESSAGES_PER_MIN", 20),
        daily_spend_cap_usd=_float("CUEFRAME_DAILY_SPEND_CAP_USD", 25.0),
        video_costs=os.environ.get("CUEFRAME_VIDEO_COSTS", ""),
        alert_webhook=os.environ.get("CUEFRAME_ALERT_WEBHOOK", ""),
        sentry_dsn=os.environ.get("SENTRY_DSN", ""),
        watermark_plans=tuple(
            p.strip()
            for p in os.environ.get("CUEFRAME_WATERMARK_PLANS", "free").split(",")
            if p.strip()
        ),
        referral_bonus=_int("CUEFRAME_REFERRAL_BONUS", 3),
        board_cpu=_float("CUEFRAME_BOARD_CPU", 8),
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
