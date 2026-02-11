from __future__ import annotations

import json
from collections.abc import Mapping

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://orra_user:orra_password@localhost:5432/orra_app"
    redis_url: str = "redis://localhost:6379/0"

    master_encryption_key: str = "replace_with_fernet_key"

    clerk_jwks_url: str = "https://example.clerk.accounts.dev/.well-known/jwks.json"
    clerk_issuer: str = "https://example.clerk.accounts.dev"
    clerk_audience: str = ""

    auth_refresh_interval_seconds: int = 86400
    auth_token_ttl_seconds: int = 72000
    auth_max_retry_delay_seconds: int = 300

    playwright_headless: bool = True
    zerodha_user_selector: str = "input#userid"
    zerodha_password_selector: str = "input#password"
    zerodha_totp_selector: str = "input#totp"
    zerodha_submit_selector: str = "button[type='submit']"
    zerodha_user_id_map_json: str = "{}"
    zerodha_password_map_json: str = "{}"

    ticker_reconnect_initial_delay_seconds: int = 2
    ticker_reconnect_max_delay_seconds: int = 120
    ticker_instrument_tokens_csv: str = ""
    super_admin_subjects_csv: str = ""

    def tenant_zerodha_users(self) -> Mapping[str, str]:
        return json.loads(self.zerodha_user_id_map_json)

    def tenant_zerodha_passwords(self) -> Mapping[str, str]:
        return json.loads(self.zerodha_password_map_json)

    def ticker_instrument_tokens(self) -> list[int]:
        if not self.ticker_instrument_tokens_csv.strip():
            return []
        return [int(token.strip()) for token in self.ticker_instrument_tokens_csv.split(",") if token.strip()]

    def super_admin_subjects(self) -> set[str]:
        if not self.super_admin_subjects_csv.strip():
            return set()
        return {value.strip() for value in self.super_admin_subjects_csv.split(",") if value.strip()}


settings = Settings()
