"""MiniMax Token Plan quota query via API Key (no _token cookie needed).

- Endpoint: GET https://www.minimaxi.com/backend/account/token_plan/remains_percent
  (override with env MINIMAX_QUOTA_URL)
- Auth: Authorization: Bearer <MINIMAX_API_KEY> (the same key used for model calls)
- Reads model_remains[] entry with model_name="general" (text models) for 5h / 7d usage.
- The API key does not expire unless revoked, so quota monitoring needs no token refresh.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests

QUOTA_API_URL = "https://www.minimaxi.com/backend/account/token_plan/remains_percent"


@dataclass(frozen=True)
class QuotaSnapshot:
    """Parsed 5h / 7d usage of the text-model (general) window."""

    used_5h_pct: int | None
    used_7d_pct: int | None
    interval_status: int | None  # 1=normal, 0=limited
    raw: dict


def _parse_pct(value) -> int | None:
    """Parse '58%' -> 58; tolerate None / int / malformed strings."""
    if isinstance(value, str) and value.endswith("%"):
        try:
            return int(value[:-1])
        except ValueError:
            return None
    if isinstance(value, int):
        return value
    return None


def fetch_quota(api_key: str, timeout: float = 15) -> QuotaSnapshot:
    """Call the quota endpoint with the MiniMax API key and return the general snapshot."""
    url = os.environ.get("MINIMAX_QUOTA_URL") or QUOTA_API_URL
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    models = data.get("model_remains", []) or []
    general = next((m for m in models if m.get("model_name") == "general"), None)
    if general is None:
        return QuotaSnapshot(None, None, None, data)
    return QuotaSnapshot(
        used_5h_pct=_parse_pct(general.get("current_interval_used_percent")),
        used_7d_pct=_parse_pct(general.get("current_weekly_used_percent")),
        interval_status=general.get("current_interval_status"),
        raw=data,
    )


def check_quota() -> QuotaSnapshot:
    """Read MINIMAX_API_KEY from env and query quota; raises on failure (caller handles)."""
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        raise RuntimeError("MINIMAX_API_KEY not set; cannot query MiniMax quota")
    return fetch_quota(api_key)
