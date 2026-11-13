"""Meta adapters. Two implementations of one interface:

* DemoMetaProvider: deterministic SIMULATED insights for sandbox (mode DEMO) ad accounts. Every figure it returns is labelled simulated and stored with source='DEMO'.
* GraphMetaProvider: the real Graph API. It can verify that an asset exists and that the configured token can read it. Insight/publishing calls are NOT implemented
  (they raise), so nothing can pretend to be live. It never stores the token; the token comes from MINTREE_META_ACCESS_TOKEN only.

A connection becomes CONNECTED only through `verify` succeeding (see the CHECK constraints on meta_connections)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

import httpx

from app.core import resilience

from app.core.config import get_settings


class MetaNotConfigured(Exception):
    pass


class MetaError(Exception):
    pass


@dataclass
class DayInsight:
    reach: int
    impressions: int
    clicks: int
    engagements: int
    results: int
    spend: Decimal
    simulated: bool


class MetaProvider(Protocol):
    def verify(self, kind: str, external_ref: str) -> dict: ...
    def insights(self, campaign_key: str, day: date, daily_budget: Decimal, objective: str | None) -> DayInsight: ...


def _h(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


class DemoMetaProvider:
    """Plausible but invented numbers, a pure function of (campaign, day): re-running it never changes a stored day."""

    def verify(self, kind: str, external_ref: str) -> dict:
        raise MetaError("Sandbox records are never verified against Meta")

    def insights(self, campaign_key: str, day: date, daily_budget: Decimal, objective: str | None) -> DayInsight:
        u = lambda salt: (_h(campaign_key, day.isoformat(), salt) % 1000) / 1000        # 0.000 .. 0.999
        spend = (daily_budget * Decimal(str(round(0.7 + 0.3 * u("s"), 3)))).quantize(Decimal("0.01"))
        cpm = Decimal(str(round(90 + 60 * u("c"), 2)))                                     # Rs per 1,000 impressions
        impressions = int(spend / cpm * 1000)
        reach = int(impressions * (0.62 + 0.2 * u("r")))
        ctr = 0.008 + 0.014 * u("t")
        clicks = min(int(impressions * ctr), impressions)
        engagements = int(reach * (0.02 + 0.05 * u("e")))
        rate = {"LEADS": 0.045, "SALES": 0.02, "TRAFFIC": 0.0, "AWARENESS": 0.0, "ENGAGEMENT": 0.0}.get(objective or "", 0.0)
        results = int(clicks * rate * (0.6 + 0.8 * u("x")))
        return DayInsight(reach, impressions, clicks, engagements, results, spend, True)


class GraphMetaProvider:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        s = get_settings()
        if not s.meta_access_token:
            raise MetaNotConfigured("No Meta access token is configured (MINTREE_META_ACCESS_TOKEN). Live connections cannot be verified.")
        self.token, self.version, self.transport = s.meta_access_token, s.meta_graph_version, transport

    def verify(self, kind: str, external_ref: str) -> dict:
        ref = external_ref if kind != "AD_ACCOUNT" or external_ref.startswith("act_") else f"act_{external_ref}"
        try:
            with httpx.Client(timeout=15, transport=self.transport) as c:
                r = resilience.call(resilience.BREAKERS["meta"], lambda: c.get(f"https://graph.facebook.com/{self.version}/{ref}", params={"fields": "id,name"}, headers={"Authorization": f"Bearer {self.token}"}))
        except resilience.CircuitOpen as e:
            raise MetaError(f"Meta is temporarily unavailable (next attempt in about {int(e.retry_in_s) + 1} s). The sandbox workspace keeps working, labelled Simulated.") from None
        except httpx.HTTPError as e:
            raise MetaError(f"Meta could not be reached ({type(e).__name__}).") from None
        if r.status_code != 200:
            raise MetaError(f"Meta refused the check (HTTP {r.status_code}); the token may lack access to this asset.")
        data = r.json()
        if str(data.get("id", "")).removeprefix("act_") != ref.removeprefix("act_"):
            raise MetaError("Meta returned a different asset than the one registered.")
        return {"id": data.get("id"), "name": data.get("name")}

    def insights(self, campaign_key: str, day: date, daily_budget: Decimal, objective: str | None) -> DayInsight:
        raise NotImplementedError("Reading live insights from the Graph API is not implemented")


def demo_provider() -> DemoMetaProvider:
    return DemoMetaProvider()
