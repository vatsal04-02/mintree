from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MINTREE_", extra="ignore")

    database_url: str = "postgresql+psycopg://mintree:mintree@localhost:5433/mintree"
    public_base_url: str = ""                      # the address people open the app at (e.g. https://factory.mintree.example); when set, QR codes on documents link straight to the record
    environment: str = "development"  # development | test | production
    # Documentation only: the business zone is a code constant (app/core/clock.py + SQL functions from migration 0015)
    # so it cannot drift per environment. All instants are stored/handled as UTC.

    # --- Rate limiting (per client IP, fixed one-minute windows; Redis-backed with an in-process fallback) ---
    rate_limit_enabled: bool = False          # enable in production (docker-compose sets it); off in development and tests so suites are not throttled
    rate_limit_login_per_min: int = 20        # /api/auth/login attempts per IP per minute (credential stuffing guard; the account lockout still applies)
    rate_limit_webhook_per_min: int = 600     # inbound courier/marketplace webhooks per IP per minute
    trust_forwarded_for: bool = False         # behind Caddy/Nginx: take the client IP from X-Forwarded-For (only enable when a trusted proxy sets it)

    # --- Connection pool & concurrency (per API process; see docs/SCALABILITY_STRATEGY.md for the capacity maths) ---
    # Total DB connections one process may hold = db_pool_size + db_max_overflow.
    db_pool_size: int = 10
    db_max_overflow: int = 0
    db_pool_timeout_s: float = 10.0       # fail fast (HTTP 503) instead of queueing for 30 s
    db_pool_recycle_s: int = 1800
    db_pre_ping: bool = True                 # SELECT 1 on every checkout: protects against stale connections, costs a round-trip
    db_prepare_threshold: str = "5"          # psycopg server-side auto-prepare after N executions; "none" disables (needed for PgBouncer txn pooling)
    # Admission control: max requests in flight per process (0 = DB pool capacity). Requests beyond it wait without
    # holding a thread or connection and are rejected with 503 after admission_timeout_s.
    max_in_flight: int = 0
    admission_timeout_s: float = 5.0
    # Thread pool for sync handlers; must be >= in-flight slots (0 = slots + 4 headroom for non-DB sync work).
    threadpool_size: int = 0
    # Messaging (Redis = cache/coordination only; RabbitMQ = durable work queue; neither is a source of truth)
    broker_url: str = "amqp://guest:guest@127.0.0.1:5672//"
    redis_url: str = "redis://127.0.0.1:6379/0"
    rabbitmq_api_url: str = "http://guest:guest@127.0.0.1:15672/api"
    outbox_batch: int = 100
    outbox_lease_s: int = 60            # a 'publishing' row older than this is considered abandoned (publisher crash)
    outbox_max_attempts: int = 8        # then status='dead' for operator inspection
    outbox_retention_days: int = 7

    # Dispatch / courier
    courier_webhook_secret: str = "dev-courier-secret"     # HMAC key for inbound courier webhooks; MUST be overridden in production
    marketplace_webhook_secret: str = "dev-marketplace-secret"
    courier_default: str = "fake"
    courier_booking_lease_s: int = 120

    # Observability
    metrics_token: str = ""            # if set, /api/metrics requires "Authorization: Bearer <token>"
    slow_request_ms: int = 1000        # log requests slower than this with a DB breakdown
    slow_tx_ms: int = 300              # log DB transactions slower than this with their slowest statements
    log_json: bool = True

    # Server-side safety nets applied to every connection (milliseconds; 0 disables).
    db_statement_timeout_ms: int = 15000
    db_lock_timeout_ms: int = 5000
    db_idle_in_tx_timeout_ms: int = 30000

    # Session / cookie security
    session_cookie_name: str = "mintree_session"
    session_ttl_hours: int = 12
    cookie_secure: bool = False  # must be True behind HTTPS in production
    cookie_samesite: str = "lax"

    # Login protection
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # Document storage (local filesystem now; S3-compatible backend is a drop-in
    # implementation of app.documents.storage.Storage)
    storage_root: str = "storage"
    max_upload_mb: int = 25

    # AI layer (docs/AI_LAYER.md). OFF by default: nothing in the system calls a model unless an administrator enables it and supplies a key.
    ai_enabled: bool = False                       # global kill switch: false = every /ai endpoint answers 503 and no model is called
    ai_provider: str = "anthropic"                 # anthropic | ollama | fake (fake = scripted demo/test model, labelled as such in the UI)
    ai_model: str = "claude-sonnet-5-5"            # assistant and briefing
    ai_extract_model: str = "claude-haiku-5-5"     # document extraction
    ai_api_key: str = ""                           # environment only; never stored in the database or returned by any endpoint
    ai_base_url: str = ""                          # empty = the provider's default (https://api.anthropic.com, http://localhost:11434)
    ai_timeout_s: float = 60.0
    ai_max_tool_rounds: int = 6
    ai_max_output_tokens: int = 1500
    ai_daily_token_budget_per_user: int = 200_000  # input + output tokens per user per business day
    ai_max_message_chars: int = 4000
    ai_max_document_mb: int = 10
    # Meta Graph API (live connection). Empty = no live connection can be verified; the demo/sandbox workspace never needs it. Environment only.
    meta_access_token: str = ""
    meta_graph_version: str = "v21.0"

    # Demo Live Mode (docs/DEMO_RUNBOOK.md). OFF by default and refused outside a demo database (app/demo_live/guard.py).
    demo_live: bool = False
    einvoice_mode: str = "off"                   # off | sandbox | live. Sandbox computes a real IRN but registers nothing; live needs a GSP adapter (accounts/einvoice.py)
    demo_live_interval_min_s: float = 20.0         # a new action every min..max seconds (jittered), divided by the speed multiplier
    demo_live_interval_max_s: float = 60.0
    demo_live_seed: int = 20261112                 # deterministic choices: same seed, same sequence of picks
    demo_live_max_events: int = 2000               # event-log rows kept
    demo_live_max_created: int = 400               # most records Live Mode may create in one run of the demo (volume cap)
    demo_live_user_prefix: str = "demo."           # live actors are the seeded demo.* users, with their normal permissions
    demo_live_password: str = "Mintree-Demo#2026"
    demo_admin_email: str = "demo.admin@example.com"
    demo_admin_session_hours: int = 72             # session length for the demo admin only (never logs out mid-presentation)

    # First-run bootstrap admin (used only by `python -m app.seed`)
    bootstrap_admin_email: str = "admin@example.com"  # override via MINTREE_BOOTSTRAP_ADMIN_EMAIL
    bootstrap_admin_password: str = ""


def production_problems(s: "Settings") -> list[str]:
    """Settings that must never reach a real deployment. Empty list = fine. Called at start-up when MINTREE_ENVIRONMENT=production (the API refuses to start otherwise)."""
    bad = []
    if s.courier_webhook_secret in ("dev-courier-secret", "", "CHANGE_ME") or s.marketplace_webhook_secret in ("dev-marketplace-secret", "", "CHANGE_ME"):
        bad.append("webhook secrets are still the development defaults (MINTREE_COURIER_WEBHOOK_SECRET / MINTREE_MARKETPLACE_WEBHOOK_SECRET)")
    if not s.cookie_secure:
        bad.append("MINTREE_COOKIE_SECURE must be true behind HTTPS")
    if not s.rate_limit_enabled:
        bad.append("MINTREE_RATE_LIMIT_ENABLED must be true")
    if "mintree:mintree@" in s.database_url:
        bad.append("the database still uses the default mintree/mintree credentials")
    if s.demo_live:
        bad.append("MINTREE_DEMO_LIVE must be off in production")
    if (s.einvoice_mode or "off").lower() == "sandbox":
        bad.append("MINTREE_EINVOICE_MODE=sandbox has no legal value; use off, or live with a configured provider")
    if s.bootstrap_admin_password and s.bootstrap_admin_password.upper().startswith("CHANGE_ME"):
        bad.append("MINTREE_BOOTSTRAP_ADMIN_PASSWORD is still the placeholder")
    return bad


@lru_cache
def get_settings() -> Settings:
    return Settings()
