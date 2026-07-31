"""Runtime configuration, read from the environment (or Docker secrets via env).

Settings are resolved once per process and injected into routers with
:data:`SettingsDep`. Services never reach for configuration themselves — they
take the roots and limits they need as arguments, which keeps them pure and lets
tests point them at a throwaway vault under ``tmp_path``.
"""

from __future__ import annotations

from functools import cached_property, lru_cache
from pathlib import Path, PurePosixPath
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The REST mount point. Shared between app/main.py (router prefix) and
# app/middleware.py (the pure-ASGI scope guard that keeps REST-only
# logging/size-limiting from ever touching /mcp — MCP_IMPLEMENTATION_PLAN
# section 15) so the two can't drift apart.
API_PREFIX = "/api/v1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    api_token: str = Field(
        min_length=16,
        description="Bearer token required by every endpoint except /api/v1/health.",
    )

    vault_read_root: Path = Field(
        default=Path("/vault-ro"),
        description="Read-only mount of the whole vault.",
    )
    vault_inbox_root: Path = Field(
        default=Path("/vault-write/inbox"),
        description="The one writable directory (00_Inbox/ChatGPT).",
    )
    vault_inbox_relative_path: str = Field(
        default="00_Inbox/ChatGPT",
        description=(
            "Where vault_inbox_root sits relative to the vault root. Needed because read "
            "and write are separate mounts, so the relative path cannot be derived."
        ),
    )

    max_search_results: int = Field(default=50, ge=1, le=200)
    max_note_size_bytes: int = Field(default=1_048_576, ge=1024)
    max_request_bytes: int = Field(default=2_097_152, ge=1024)

    tz: str = Field(default="Asia/Tokyo", description="Timezone for modified_at timestamps.")
    log_level: str = Field(default="INFO")

    @field_validator("api_token")
    @classmethod
    def _strip_token(cls, value: str) -> str:
        return value.strip()

    @field_validator("vault_inbox_relative_path")
    @classmethod
    def _clean_inbox_relative_path(cls, value: str) -> str:
        cleaned = value.strip().strip("/")
        parts = PurePosixPath(cleaned).parts if cleaned else ()
        if not cleaned or "\\" in cleaned or any(part in {"..", "."} for part in parts):
            msg = "VAULT_INBOX_RELATIVE_PATH must be a clean vault-relative directory"
            raise ValueError(msg)
        return cleaned

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.strip().upper()

    # The mount points themselves are resolved once here and are *not* subject to
    # the per-component symlink rejection in path_security: a deployment is
    # allowed to mount the vault through a symlink. Everything below the root is
    # still checked component by component.
    @cached_property
    def read_root(self) -> Path:
        return self.vault_read_root.resolve()

    @cached_property
    def inbox_root(self) -> Path:
        return self.vault_inbox_root.resolve()

    @cached_property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Call ``get_settings.cache_clear()`` in tests."""
    return Settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]
