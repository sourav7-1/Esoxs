"""Tiny LLM layer. The AI only writes a compact spec (Markdown / Mermaid / JSON).
No SDKs needed — plain HTTP via `requests`.

Providers (pick with --provider or SPECRENDER_PROVIDER, otherwise auto-detected from keys):
  anthropic  ANTHROPIC_API_KEY
  openai     OPENAI_API_KEY   (+ OPENAI_BASE_URL for Groq / OpenRouter / any OpenAI-compatible API)
  gemini     GEMINI_API_KEY
  ollama     local, free, no key (OLLAMA_HOST, default http://localhost:11434)
"""
from __future__ import annotations

import json
import os
import re

import requests

# Load KEY=value lines from specrender/.env; real env vars win.
_ENV = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_ENV):
    for _line in open(_ENV, encoding="utf-8"):
        _k, _, _v = _line.strip().partition("=")
        if _k and not _k.startswith("#") and _v:
            os.environ.setdefault(_k.strip(), _v.strip().strip('"\''))

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.5-flash",
    "ollama": "llama3.2",
}

# Short, fixed system prompts. The AI never sees CSS or layout — only the spec format.
PROMPTS = {
    "doc": """You write the content of a document as Markdown. Output ONLY the Markdown.
Start with frontmatter:
---
title: <title>
subtitle: <one line>
kicker: <2-3 word category>
---
Then use ## and ### headings, short paragraphs, bullet lists, tables, > for key notes.
For any process, flow or architecture add a ```mermaid block (prefer flowchart TD, max 12 nodes).
No HTML, no CSS, no images, no commentary before or after. Write in the language of the request.""",

    "diagram": """Output ONLY valid Mermaid v11 code — no code fences, no commentary.
First line: %% title: <short title>
Pick the best type: flowchart TD/LR, sequenceDiagram, classDiagram, erDiagram, stateDiagram-v2, mindmap, timeline, gantt.
Put labels with special characters in double quotes, e.g. A["Login (OTP)"]. Max 25 nodes. No styling or classDef.""",

    "poster": """Output ONLY one JSON object, no code fences, no commentary. Schema:
{"kind":"poster","size":"portrait|square|story|landscape|a4","tag":"2-3 words",
 "title":"max 6 words, wrap ONE key word in *stars* to highlight it","subtitle":"one sentence",
 "stats":[{"value":"short","label":"2-4 words"}],
 "sections":[{"heading":"1-3 words","points":["max 8 words each"]}],
 "footer":"short line"}
Use 0-3 stats (only real or user-given numbers), 2-6 sections with 2-4 points each. Write in the language of the request.""",

    "chart": """Output ONLY one JSON object, no code fences, no commentary. Schema:
{"kind":"chart","type":"bar|line|pie|donut","title":"...","labels":["..."],
 "series":[{"name":"...","values":[numbers, same length as labels]}],"unit":"optional short suffix like % or h","note":"optional source"}
Use the numbers the user gives. If they gave none, use well-known public figures and put the source in "note";
if you are not sure, set "note":"Illustrative data". pie/donut use only the first series.""",
}

FIX_PROMPT = "That failed to render with this error:\n{error}\nReturn the corrected version only, same format, nothing else."


class LLMError(Exception):
    pass


def pick_provider(provider: str | None) -> str:
    provider = provider or os.getenv("SPECRENDER_PROVIDER")
    if provider:
        if provider not in DEFAULT_MODELS:
            raise LLMError(f"Unknown provider '{provider}'. Use: {', '.join(DEFAULT_MODELS)}")
        return provider
    for name, env in (("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY"), ("gemini", "GEMINI_API_KEY")):
        if os.getenv(env):
            return name
    return "ollama"


def _need(env: str) -> str:
    v = os.getenv(env)
    if not v:
        raise LLMError(f"Set {env} (or pick another provider with --provider)")
    return v


def _post(url: str, **kw) -> dict:
    try:
        r = requests.post(url, timeout=180, **kw)
    except requests.RequestException as e:
        raise LLMError(f"Could not reach {url.split('?')[0]}: {e}")
    if r.status_code >= 400:
        raise LLMError(f"API error {r.status_code}: {r.text[:400]}")
    return r.json()


def chat(system: str, messages: list[dict], provider: str, model: str, max_tokens: int = 4000) -> tuple[str, dict]:
    """messages: [{"role": "user"|"assistant", "content": str}]. Returns (text, {"in": n, "out": n})."""
    if provider == "anthropic":
        data = _post("https://api.anthropic.com/v1/messages",
                     headers={"x-api-key": _need("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01",
                              "content-type": "application/json"},
                     json={"model": model, "max_tokens": max_tokens, "messages": messages,
                           "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]})
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        u = data.get("usage", {})
        return text, {"in": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0), "out": u.get("output_tokens", 0)}

    if provider == "openai":
        base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        data = _post(f"{base}/chat/completions",
                     headers={"Authorization": f"Bearer {_need('OPENAI_API_KEY')}"},
                     json={"model": model, "max_tokens": max_tokens,
                           "messages": [{"role": "system", "content": system}] + messages})
        u = data.get("usage", {})
        return data["choices"][0]["message"]["content"] or "", {"in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0)}

    if provider == "gemini":
        data = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                     headers={"x-goog-api-key": _need("GEMINI_API_KEY")},
                     json={"systemInstruction": {"parts": [{"text": system}]},
                           "contents": [{"role": "model" if m["role"] == "assistant" else "user",
                                         "parts": [{"text": m["content"]}]} for m in messages],
                           "generationConfig": {"maxOutputTokens": max_tokens}})
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError):
            raise LLMError(f"Gemini returned no text: {json.dumps(data)[:300]}")
        u = data.get("usageMetadata", {})
        return text, {"in": u.get("promptTokenCount", 0), "out": u.get("candidatesTokenCount", 0)}

    if provider == "ollama":
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        data = _post(f"{host}/api/chat", json={"model": model, "stream": False,
                                               "messages": [{"role": "system", "content": system}] + messages})
        return data["message"]["content"], {"in": data.get("prompt_eval_count", 0), "out": data.get("eval_count", 0)}

    raise LLMError(f"Unknown provider '{provider}'")


def clean(kind: str, text: str) -> str:
    """Strip code fences / chatter the model may add anyway."""
    text = text.strip()
    if kind in ("poster", "chart"):
        a, b = text.find("{"), text.rfind("}")
        return text[a:b + 1] if a != -1 and b > a else text
    m = re.match(r"^```[\w-]*\s*\n(.*?)\n```\s*$", text, re.S)
    if m:
        text = m.group(1).strip()
    elif kind == "diagram" and "```" in text:  # fenced block inside chatter
        m = re.search(r"```(?:mermaid)?\s*\n(.*?)```", text, re.S)
        if m:
            text = m.group(1).strip()
    return text
