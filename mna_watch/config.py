"""Settings, all read from environment variables (GitHub Actions secrets/vars or a local .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else default


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in _env(name, default).split(",") if x.strip()]


ALL_SOURCES = "sec,uk_panel,euronext,eqs,globenewswire,prnewswire,bse"
DEFAULT_ALERT_STAGES = "rumour,possible_offer,definitive_agreement,tender_offer,revised_offer,competing_offer"


@dataclass
class Settings:
    # Which sources to poll
    sources: list[str] = field(default_factory=lambda: _list("SOURCES", ALL_SOURCES))

    # Which deal stages trigger an email
    alert_stages: list[str] = field(default_factory=lambda: _list("ALERT_STAGES", DEFAULT_ALERT_STAGES))

    # SEC requires a descriptive User-Agent with contact details
    sec_user_agent: str = field(default_factory=lambda: _env("SEC_USER_AGENT", ""))

    # LLM: "gemini" (free tier), "anthropic", or "none" (keywords only)
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "auto").lower())
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-2.5-flash"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-haiku-5-5"))
    max_llm_calls_per_cycle: int = field(default_factory=lambda: int(_env("MAX_LLM_CALLS_PER_CYCLE", "40")))

    # Email (any SMTP server; Gmail with an App Password works)
    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "465")))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    email_to: list[str] = field(default_factory=lambda: _list("EMAIL_TO", ""))
    email_from: str = field(default_factory=lambda: _env("EMAIL_FROM"))

    # Behaviour
    state_dir: str = field(default_factory=lambda: _env("STATE_DIR", "state"))
    poll_seconds: int = field(default_factory=lambda: int(_env("POLL_SECONDS", "90")))
    # On the very first run, record everything currently in the feeds as "seen" without emailing,
    # so you don't get a flood of old announcements.
    bootstrap_silently: bool = field(default_factory=lambda: _bool("BOOTSTRAP_SILENTLY", True))
    # Re-alert on the same target only if the stage changes (e.g. possible offer -> firm offer)
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", False))

    def resolved_llm(self) -> str:
        if self.llm_provider in {"gemini", "anthropic", "none"}:
            return self.llm_provider
        if self.gemini_api_key:
            return "gemini"
        if self.anthropic_api_key:
            return "anthropic"
        return "none"

    def email_ready(self) -> bool:
        return bool(self.smtp_user and self.smtp_password and self.email_to)
