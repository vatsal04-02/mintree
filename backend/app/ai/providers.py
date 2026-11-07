"""Model providers behind one interface. A provider only turns (system, messages, tools) into a completion; it never touches the
database and never sees credentials other than its own API key, which comes from the environment (never stored, never logged).

Providers: `anthropic` (Messages API over httpx, no SDK dependency), `ollama` (local), `fake` (scripted, for tests and the demo; labelled in the UI)."""
from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import httpx

from app.core.config import get_settings


class ProviderError(Exception):
    """Provider unreachable, refused, or returned something unusable. Safe to show to the user (no secrets in the message)."""


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict


@dataclass
class Completion:
    text: str = ""
    tool_uses: list[ToolUse] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    stop: str = "end_turn"
    raw_content: list[dict] | None = None      # provider-native assistant content, replayed on the next round


class Provider(Protocol):
    name: str
    def complete(self, *, system: str, messages: list[dict], tools: list[dict], model: str, max_tokens: int, force_tool: str | None = None) -> Completion: ...


def _dec_loads(s: str | bytes) -> Any:
    """Parse JSON keeping numbers exact (floats would corrupt money)."""
    return json.loads(s, parse_float=Decimal)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        s = get_settings()
        if not s.ai_api_key:
            raise ProviderError("The AI provider is not configured (no API key in the environment).")
        self.key, self.base, self.timeout, self.transport = s.ai_api_key, (s.ai_base_url or "https://api.anthropic.com").rstrip("/"), s.ai_timeout_s, transport

    def complete(self, *, system, messages, tools, model, max_tokens, force_tool=None) -> Completion:
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            body["tools"] = tools
            if force_tool:
                body["tool_choice"] = {"type": "tool", "name": force_tool}
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as c:
                r = c.post(f"{self.base}/v1/messages", json=body,
                           headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        except httpx.HTTPError as e:
            raise ProviderError(f"The AI provider could not be reached ({type(e).__name__}).") from None
        if r.status_code >= 400:
            raise ProviderError(f"The AI provider refused the request (HTTP {r.status_code}).")
        try:
            data = _dec_loads(r.content)
            blocks = data["content"]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            uses = [ToolUse(b["id"], b["name"], b.get("input") or {}) for b in blocks if b.get("type") == "tool_use"]
            u = data.get("usage") or {}
            return Completion(text, uses, int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)), data.get("stop_reason", "end_turn"), _jsonable(blocks))
        except (KeyError, TypeError, ValueError):
            raise ProviderError("The AI provider returned an unreadable response.") from None


def _jsonable(o: Any) -> Any:
    return json.loads(json.dumps(o, default=str))


class OllamaProvider:
    """Local model through Ollama's /api/chat. Tool calling depends on the model; no data leaves the premises."""

    name = "ollama"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        s = get_settings()
        self.base, self.timeout, self.transport = (s.ai_base_url or "http://localhost:11434").rstrip("/"), s.ai_timeout_s, transport

    def complete(self, *, system, messages, tools, model, max_tokens, force_tool=None) -> Completion:
        msgs = [{"role": "system", "content": system}]
        for m in messages:
            c = m["content"]
            if isinstance(c, str):
                msgs.append({"role": m["role"], "content": c})
                continue
            for b in c:       # flatten Anthropic-style blocks
                if b.get("type") == "text":
                    msgs.append({"role": m["role"], "content": b["text"]})
                elif b.get("type") == "tool_result":
                    msgs.append({"role": "tool", "content": b["content"] if isinstance(b["content"], str) else json.dumps(b["content"])})
        body: dict[str, Any] = {"model": model, "messages": msgs, "stream": False, "options": {"num_predict": max_tokens}}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as c:
                r = c.post(f"{self.base}/api/chat", json=body)
            r.raise_for_status()
            data = _dec_loads(r.content)
            msg = data["message"]
            uses = [ToolUse(f"ollama-{i}", t["function"]["name"], t["function"].get("arguments") or {}) for i, t in enumerate(msg.get("tool_calls") or [])]
            return Completion(msg.get("content", ""), uses, int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0)),
                              "tool_use" if uses else "end_turn", None)
        except httpx.HTTPError as e:
            raise ProviderError(f"The local AI model could not be reached ({type(e).__name__}).") from None
        except (KeyError, TypeError, ValueError):
            raise ProviderError("The local AI model returned an unreadable response.") from None


class FakeProvider:
    """Deterministic stand-in. Tests push scripted `Completion`s onto `script`; with an empty script it answers from keyword rules so the demo
    works without a key. It is intentionally simple: it proves the plumbing (tools, permissions, citations, audit), not model quality."""

    name = "fake"
    script: deque[Completion] = deque()
    calls: list[dict] = []          # every request seen, for assertions

    def complete(self, *, system, messages, tools, model, max_tokens, force_tool=None) -> Completion:
        FakeProvider.calls.append({"system": system, "messages": json.loads(json.dumps(messages, default=str)), "tools": [t["name"] for t in tools], "model": model, "force_tool": force_tool})
        if FakeProvider.script:
            return FakeProvider.script.popleft()
        if force_tool == "record_extraction":
            return Completion(tool_uses=[ToolUse("fake-ext", "record_extraction", _fake_extract(messages))], tokens_in=50, tokens_out=50, stop="tool_use")
        last = messages[-1]
        # after a tool round: summarise whatever the tools returned, quoting numbers verbatim
        if isinstance(last["content"], list) and any(b.get("type") == "tool_result" for b in last["content"]):
            parts = [_summarise(str(b["content"])) for b in last["content"] if b.get("type") == "tool_result"]
            return Completion("Here is what the records show:\n" + "\n".join(parts), tokens_in=60, tokens_out=40)
        q = (last["content"] if isinstance(last["content"], str) else "").lower()
        names = {t["name"] for t in tools}
        for kw, tool, args in (("overdue", "receivables", {}), ("receivable", "receivables", {}), ("late", "late_purchase_orders", {}), ("low stock", "low_stock", {}),
                               ("reorder", "low_stock", {}), ("overview", "business_overview", {}), ("quality", "quality_status", {}), ("production", "production_status", {})):
            if kw in q and tool in names:
                return Completion(tool_uses=[ToolUse(f"fake-{tool}", tool, args)], tokens_in=40, tokens_out=10, stop="tool_use")
        m = re.search(r"stock (?:of|for) ([\w\-\. ]+)", q)
        if m and "stock_availability" in names:
            return Completion(tool_uses=[ToolUse("fake-stock", "stock_availability", {"item": m.group(1).strip()})], tokens_in=40, tokens_out=10, stop="tool_use")
        return Completion("I can look up stock, receivables, late purchase orders, quality, production and the business overview. Ask about one of those.", tokens_in=30, tokens_out=20)


def _summarise(tool_content: str) -> str:
    """Readable rendering of a tool result for the demo provider: scalars as `key: value`, lists as a count plus their first entries. Values are copied, never computed."""
    body = re.sub(r"^<tool_data>|</tool_data>$", "", tool_content.strip())
    try:
        data = json.loads(body)
    except ValueError:
        return body[:300]
    def show(v: Any) -> str:
        if isinstance(v, dict):
            return ", ".join(f"{k} {x}" for k, x in v.items() if not isinstance(x, (dict, list)))
        return str(v)
    lines = []
    for k, v in (data.items() if isinstance(data, dict) else [("result", data)]):
        label = k.replace("_", " ")
        if isinstance(v, list):
            lines.append(f"- {label}: {len(v)}" + ("".join(f"\n    · {show(x)}" for x in v[:5])))
        elif isinstance(v, dict):
            lines.append(f"- {label}: {show(v)}")
        elif v is not None:
            lines.append(f"- {label}: {v}")
    return "\n".join(lines) or "Nothing found."


def _doc_text(messages: list[dict]) -> str:
    out = []
    for m in messages:
        c = m["content"]
        out += [c] if isinstance(c, str) else [b.get("text", "") for b in c if b.get("type") == "text"]
    return "\n".join(out)


def _fake_extract(messages: list[dict]) -> dict:
    """Reads simple `Label: value` text documents (the demo and the tests use them). Line format: `LINE: <description> | <qty> | <unit> | <rate>`."""
    t = _doc_text(messages)
    g = lambda label: (re.search(rf"^{label}\s*:\s*(.+)$", t, re.M | re.I) or [None, None])[1]
    lines = []
    for m in re.finditer(r"^LINE\s*:\s*(.+?)\s*\|\s*([\d.]+)\s*\|\s*(\w*)\s*\|\s*([\d.]+)\s*$", t, re.M | re.I):
        lines.append({"description": m[1], "qty": m[2], "unit": m[3] or None, "unit_price": m[4], "tax_rate": g("GST") or None})
    return {"document_type": (g("TYPE") or "INVOICE").upper(), "supplier_name": g("SUPPLIER"), "supplier_gstin": g("GSTIN"), "document_no": g("INVOICE NO") or g("DC NO"),
            "document_date": g("DATE"), "po_number": g("PO"), "subtotal": g("SUBTOTAL"), "tax_total": g("TAX"), "total": g("TOTAL"), "lines": lines, "confidence": "0.9"}


def get_provider() -> Provider:
    s = get_settings()
    if s.ai_provider == "anthropic":
        return AnthropicProvider()
    if s.ai_provider == "ollama":
        return OllamaProvider()
    if s.ai_provider == "fake":
        return FakeProvider()
    raise ProviderError(f"Unknown AI provider '{s.ai_provider}'.")
