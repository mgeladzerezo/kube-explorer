from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./kube_explorer.db"

    # "fixture" serves recorded cluster state from disk so the app runs without a cluster.
    kube_mode: Literal["in-cluster", "kubeconfig", "fixture"] = "kubeconfig"
    kube_fixture_dir: str = "../faults"
    watch_timeout_seconds: int = 300

    llm_mode: Literal["anthropic", "fake"] = "anthropic"
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    anthropic_max_tokens: int = 16000
    # Re-runs a request on Anthropic's recommended fallback model when a safety
    # classifier declines it; cluster logs occasionally look like security content.
    anthropic_refusal_fallback: bool = True

    log_tail_lines: int = 80
    cors_origins: list[str] = ["http://localhost:5175"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
