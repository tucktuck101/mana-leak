"""Configuration (`docs/contracts.md` -> Configuration, Operational limits).

One `pydantic-settings` class, env vars then `.env`. Only the variables and
limits M1 actually reaches are defined here; the rest of `docs/contracts.md`'s
Configuration table (`EMBEDDING_MODEL`, `SPELLBOOK_BASE_URL`,
`JUDGE_MAX_CLARIFICATIONS`, ...) belongs to the milestone that introduces the
surface using it, since nothing in M1 reads them yet.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    # Required (contracts.md -> Configuration).
    database_url: SecretStr
    openrouter_api_key: SecretStr
    chat_model: str

    log_level: str = "INFO"

    # Operational limits (contracts.md -> Operational limits), reachable in
    # M1: the model-call timeout and turn deadline the gateway/orchestrator
    # enforce themselves (NFR-1), the per-turn model-call cap the gateway
    # raises `model_limit_exceeded` at, the per-call `max_tokens`, the
    # 8,000-char user-message validation limit (step 1), and the 10-turn
    # context window `build_context` uses.
    model_call_timeout_s: int = 30
    turn_timeout_s: int = 120
    model_calls_max: int = 8
    max_tokens: int = 1500
    max_user_message_chars: int = 8000
    context_turns: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
