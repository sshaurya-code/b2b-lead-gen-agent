"""GST verification (optional).

A company's GSTIN is extracted from its own website (see utils.extract_gstin);
this module confirms the GSTIN is registered and active via a pluggable GST
verification API. When no GST_API_KEY is configured, verification is skipped and
the GSTIN is recorded as found-but-unverified (the "GST verified & active"
qualification point is only awarded on a confirmed active status).

The default provider follows the common ``?gstNo=<gstin>&key_secret=<key>``
pattern; set GST_API_URL for a different provider. Parsing is tolerant of a few
common response shapes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import Config

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)


def _parse_response(payload: dict, gstin: str) -> dict:
    """Best-effort extraction of status + legal name across provider shapes."""
    info = payload.get("taxpayerInfo") or payload.get("data") or payload
    status = (
        info.get("sts")
        or info.get("status")
        or info.get("gstnStatus")
        or payload.get("status")
    )
    legal_name = info.get("lgnm") or info.get("legal_name") or info.get("tradeNam")
    active = bool(status) and str(status).strip().lower() in ("active", "act")
    return {"gstin": gstin, "status": status, "verified": active, "legal_name": legal_name}


class GstVerifier:
    def __init__(self, cfg: Config, client: "httpx.AsyncClient"):
        self.cfg = cfg
        self.client = client
        self.enabled = bool(cfg.gst_api_key)
        if not self.enabled:
            logger.info("GST_API_KEY not set — GSTINs will be recorded but not verified.")

    async def verify(self, gstin: str | None) -> dict | None:
        """Return a gst dict ({gstin,status,verified,legal_name}) or None."""
        if not gstin:
            return None
        unverified = {"gstin": gstin, "status": None, "verified": False, "legal_name": None}
        if not self.enabled:
            return unverified
        try:
            resp = await self.client.get(
                self.cfg.gst_api_url,
                params={"gstNo": gstin, "key_secret": self.cfg.gst_api_key},
                timeout=10.0,
            )
        except Exception as exc:  # noqa: BLE001 - verification is best-effort
            logger.warning("GST verification request failed for %s: %s", gstin, exc)
            return unverified
        if resp.status_code != 200:
            logger.warning("GST API HTTP %s for %s", resp.status_code, gstin)
            return unverified
        try:
            return _parse_response(resp.json(), gstin)
        except ValueError:
            return unverified
