"""Settings from the environment. No secret has a default (see infra/.env.example)."""

import os


class ConfigError(RuntimeError):
    """Required configuration is missing."""


def dsn() -> str:
    """The `cia` database on the Postgres instance of the BIS stack, with its own role."""
    host = os.getenv("CIA_PG_HOST", "127.0.0.1")
    port = os.getenv("CIA_PG_PORT", "55432")
    database = os.getenv("CIA_PG_DB", "cia")
    user = os.getenv("CIA_PG_USER", "cia")
    password = os.getenv("CIA_PG_PASSWORD")
    if not password:
        raise ConfigError("CIA_PG_PASSWORD is not set (see infra/.env.example)")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}?connect_timeout=5"


def webhook_secret() -> str:
    """Shared secret with the BIS outbox (X-BIS-Signature)."""
    secret = os.getenv("CIA_WEBHOOK_SECRET")
    if not secret:
        raise ConfigError("CIA_WEBHOOK_SECRET is not set (see infra/.env.example)")
    return secret


# --- BIS API (read-only tools) ----------------------------------------------------------------


def bis_api_url() -> str:
    """Base URL of the BIS API; compose sets http://api:8000, local runs use the published port."""
    return os.getenv("CIA_BIS_API_URL", "http://127.0.0.1:8102").rstrip("/")


def bis_client_credentials() -> tuple[str, str, str] | None:
    """(token URL, client id, secret) of the Keycloak client `cia-agent`, or None when the BIS API
    runs without authentication (BIS_AUTH_ENABLED=false, local stack)."""
    token_url = os.getenv("CIA_OIDC_TOKEN_URL")
    if not token_url:
        return None
    secret = os.getenv("CIA_AGENT_CLIENT_SECRET")
    if not secret:
        raise ConfigError("CIA_OIDC_TOKEN_URL is set but CIA_AGENT_CLIENT_SECRET is not")
    return token_url, os.getenv("CIA_AGENT_CLIENT_ID", "cia-agent"), secret


# --- LLM (plan section 5.7) -------------------------------------------------------------------

ROLES = ("central", "department")
DEFAULT_MODELS = {"central": "mistral-medium-latest", "department": "mistral-small-latest"}


def llm_provider() -> str:
    """`mistral`, or `none` to run without agent text (fallback mode)."""
    return os.getenv("CIA_LLM_PROVIDER", "mistral").lower()


def llm_model(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}")
    return os.getenv(f"CIA_LLM_MODEL_{role.upper()}", DEFAULT_MODELS[role])


def mistral_api_key() -> str:
    """Empty when unset: the provider then fails per call and the run falls back (plan section 9)."""
    return os.getenv("CIA_MISTRAL_API_KEY", "")


def mistral_data_classes() -> frozenset[str]:
    """The free tier may train on inputs, so it is cleared for `public` only by default."""
    raw = os.getenv("CIA_MISTRAL_DATA_CLASSES", "public")
    return frozenset(c.strip() for c in raw.split(",") if c.strip())


def llm_price_eur_per_mtok() -> tuple[float, float]:
    """(input, output) price per million tokens; 0 on the free tier."""
    return (
        float(os.getenv("CIA_LLM_PRICE_IN_EUR_PER_MTOK", "0")),
        float(os.getenv("CIA_LLM_PRICE_OUT_EUR_PER_MTOK", "0")),
    )


# --- central agent ----------------------------------------------------------------------------


def max_topics() -> int:
    """Topics per report (plan section 5.1: start with 3)."""
    return int(os.getenv("CIA_MAX_TOPICS", "3"))


def run_token_budget() -> int:
    """Tokens (in + out) per run; above it the run ends with the report without agent text."""
    return int(os.getenv("CIA_RUN_TOKEN_BUDGET", "60000"))


def nightly_at() -> str:
    """HH:MM (container clock, UTC) of the nightly fallback run while no daily_snapshot.v1 arrives;
    empty disables it."""
    return os.getenv("CIA_NIGHTLY_AT", "03:00")
