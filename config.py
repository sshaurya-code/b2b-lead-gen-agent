"""Configuration loading and validation (Section 9).

All environment variables are loaded at startup via ``load_dotenv()`` and
exposed through a single frozen ``Config`` object. Validation that must halt the
run (FR-31: unrecognised SEARCH_PROVIDER; missing provider keys) happens here so
failures surface immediately rather than mid-pipeline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ACCEPTED_SEARCH_PROVIDERS = ("google_cse", "serpapi", "brave")
ACCEPTED_STORAGE_BACKENDS = ("sqlite", "json")


class ConfigError(Exception):
    """Raised for unrecoverable configuration problems (exit code 1)."""


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Config:
    apollo_api_key: str | None
    search_provider: str
    google_cse_api_key: str | None
    google_cse_id: str | None
    serpapi_api_key: str | None
    brave_api_key: str | None
    proxy_list: list[str]
    max_concurrent_browsers: int
    queries_per_run: int
    output_dir: Path
    storage_backend: str
    google_cse_daily_limit: int
    llm_signal_scoring: bool
    llm_api_key: str | None
    llm_model: str

    @property
    def proxies(self) -> list[dict[str, str]]:
        """Parse PROXY_LIST entries (host:port:user:pass) into dicts."""
        parsed: list[dict[str, str]] = []
        for line in self.proxy_list:
            parts = line.split(":")
            if len(parts) != 4:
                continue
            host, port, user, password = parts
            parsed.append(
                {
                    "server": f"http://{host}:{port}",
                    "username": user,
                    "password": password,
                }
            )
        return parsed


def load_config() -> Config:
    """Load and validate configuration. Raises ``ConfigError`` on fatal issues."""
    load_dotenv()

    search_provider = (os.getenv("SEARCH_PROVIDER") or "google_cse").strip().lower()
    if search_provider not in ACCEPTED_SEARCH_PROVIDERS:
        raise ConfigError(
            f"Unrecognised SEARCH_PROVIDER={search_provider!r}. "
            f"Accepted values: {', '.join(ACCEPTED_SEARCH_PROVIDERS)}."
        )

    storage_backend = (os.getenv("STORAGE_BACKEND") or "sqlite").strip().lower()
    if storage_backend not in ACCEPTED_STORAGE_BACKENDS:
        raise ConfigError(
            f"Unrecognised STORAGE_BACKEND={storage_backend!r}. "
            f"Accepted values: {', '.join(ACCEPTED_STORAGE_BACKENDS)}."
        )

    proxy_raw = os.getenv("PROXY_LIST") or ""
    proxy_list = [ln.strip() for ln in proxy_raw.splitlines() if ln.strip()]

    llm_signal_scoring = _get_bool("LLM_SIGNAL_SCORING", False)
    llm_api_key = os.getenv("LLM_API_KEY")

    cfg = Config(
        apollo_api_key=os.getenv("APOLLO_API_KEY"),
        search_provider=search_provider,
        google_cse_api_key=os.getenv("GOOGLE_CSE_API_KEY"),
        google_cse_id=os.getenv("GOOGLE_CSE_ID"),
        serpapi_api_key=os.getenv("SERPAPI_API_KEY"),
        brave_api_key=os.getenv("BRAVE_API_KEY"),
        proxy_list=proxy_list,
        max_concurrent_browsers=_get_int("MAX_CONCURRENT_BROWSERS", 3),
        queries_per_run=_get_int("QUERIES_PER_RUN", 20),
        output_dir=Path(os.getenv("OUTPUT_DIR") or "./leads"),
        storage_backend=storage_backend,
        google_cse_daily_limit=_get_int("GOOGLE_CSE_DAILY_LIMIT", 100),
        llm_signal_scoring=llm_signal_scoring,
        llm_api_key=llm_api_key,
        llm_model=os.getenv("LLM_MODEL") or "gpt-4o-mini",
    )

    _validate_required_keys(cfg)
    return cfg


def _validate_required_keys(cfg: Config) -> None:
    missing: list[str] = []
    if not cfg.apollo_api_key:
        missing.append("APOLLO_API_KEY")

    if cfg.search_provider == "google_cse":
        if not cfg.google_cse_api_key:
            missing.append("GOOGLE_CSE_API_KEY (required for SEARCH_PROVIDER=google_cse)")
        if not cfg.google_cse_id:
            missing.append("GOOGLE_CSE_ID (required for SEARCH_PROVIDER=google_cse)")
    elif cfg.search_provider == "serpapi" and not cfg.serpapi_api_key:
        missing.append("SERPAPI_API_KEY (required for SEARCH_PROVIDER=serpapi)")
    elif cfg.search_provider == "brave" and not cfg.brave_api_key:
        missing.append("BRAVE_API_KEY (required for SEARCH_PROVIDER=brave)")

    if cfg.llm_signal_scoring and not cfg.llm_api_key:
        missing.append("LLM_API_KEY (required when LLM_SIGNAL_SCORING=true)")

    if missing:
        raise ConfigError(
            "Missing required environment variables:\n  - "
            + "\n  - ".join(missing)
            + "\nSee .env.example and Section 9 of the PRD."
        )
