#!/usr/bin/env python3
"""specrender — AI writes a tiny spec, your machine renders the PDF / image.

Render a spec you already have (0 tokens):
  python render.py examples/plan.md                 -> out/plan.pdf
  python render.py examples/login-flow.mmd          -> out/login-flow.png
  python render.py examples/poster.json -o out/p.pdf --theme dark

Let the AI write the spec, then render it:
  python render.py --ai -t diagram "TCP 3-way handshake"
  python render.py --ai -t doc plan.md              (a file works as the prompt too)
  python render.py --ai -t poster "DBMS normalization 1NF to 3NF" --theme forest

The generated spec is saved next to the output (e.g. out/tcp.spec.mmd).
Edit it and render again for free — no AI call needed.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from llm import DEFAULT_MODELS, FIX_PROMPT, PROMPTS, LLMError, chat, clean, pick_provider  # noqa: E402
from renderers import DEFAULT_EXT, KINDS, RenderError, infer_kind, render  # noqa: E402
from themes import THEMES  # noqa: E402

SPEC_EXT = {"doc": ".md", "diagram": ".mmd", "poster": ".json", "chart": ".json"}
CACHE = ROOT / ".cache"


def slugify(text: str, fallback: str = "output") -> str:
    s = re.sub(r"[^\w]+", "-", text.lower(), flags=re.UNICODE).strip("-")
    return (s[:40].strip("-") or fallback)


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="AI writes a compact spec, your machine renders it.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("input", help="spec file to render, or (with --ai) a prompt / prompt file")
    ap.add_argument("-t", "--type", choices=KINDS, help="doc | diagram | poster | chart (guessed from file extension if omitted)")
    ap.add_argument("-o", "--out", help="output path: .pdf .png .svg .html (default: out/<name>.<pdf|png>)")
    ap.add_argument("--theme", default="light", choices=list(THEMES))
    ap.add_argument("--ai", action="store_true", help="ask the AI to write the spec from the prompt")
    ap.add_argument("--provider", choices=list(DEFAULT_MODELS), help="anthropic | openai | gemini | ollama")
    ap.add_argument("--model", help="model name (default depends on provider)")
    ap.add_argument("--no-cache", action="store_true", help="don't reuse a cached spec for the same prompt")
    ap.add_argument("--fixes", type=int, default=2, help="how many times the AI may fix a spec that fails to render")
    args = ap.parse_args()

    src = Path(args.input)
    is_file = src.exists() and src.is_file()

    # ------------------------------------------------------------------ no AI: just render
    if not args.ai:
        if not is_file:
            log(f"File not found: {src}\n(Use --ai to send text to the AI.)")
            return 1
        text = src.read_text(encoding="utf-8")
        try:
            kind = args.type or infer_kind(src, text)
            out = Path(args.out) if args.out else ROOT / "out" / (src.stem + DEFAULT_EXT[kind])
            t = time.time()
            render(kind, text, out, args.theme)
        except RenderError as e:
            log(f"Render failed: {e}")
            return 1
        log(f"✓ {kind} → {out}  ({time.time() - t:.1f}s, 0 tokens)")
        return 0

    # ------------------------------------------------------------------ AI mode
    if not args.type:
        log("With --ai, pass --type doc|diagram|poster|chart")
        return 1
    kind = args.type
    prompt = src.read_text(encoding="utf-8") if is_file else args.input
    name = src.stem if is_file else slugify(prompt)
    out = Path(args.out) if args.out else ROOT / "out" / (name + DEFAULT_EXT[kind])
    spec_path = out.with_name(out.stem + ".spec" + SPEC_EXT[kind])

    try:
        provider = pick_provider(args.provider)
    except LLMError as e:
        log(str(e))
        return 1
    model = args.model or __import__("os").getenv("SPECRENDER_MODEL") or DEFAULT_MODELS[provider]
    key = hashlib.sha256(f"{kind}|{provider}|{model}|{prompt}".encode()).hexdigest()[:16]
    cache_file = CACHE / (key + SPEC_EXT[kind])

    system = PROMPTS[kind]
    messages = [{"role": "user", "content": prompt}]
    total = {"in": 0, "out": 0}

    if cache_file.exists() and not args.no_cache:
        spec = cache_file.read_text(encoding="utf-8")
        log("• Using cached spec (0 tokens). Pass --no-cache to regenerate.")
    else:
        log(f"• Asking {provider}/{model} for a {kind} spec…")
        try:
            raw, usage = chat(system, messages, provider, model)
        except LLMError as e:
            log(f"AI call failed: {e}")
            return 1
        total = {k: total[k] + usage[k] for k in total}
        spec = clean(kind, raw)

    for attempt in range(args.fixes + 1):
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(spec, encoding="utf-8")
        try:
            t = time.time()
            render(kind, spec, out, args.theme)
            break
        except RenderError as e:
            if attempt == args.fixes:
                log(f"Render failed after {args.fixes} fix attempts: {e}\nSpec saved at {spec_path} — edit it and run:\n"
                    f"  python render.py {spec_path} -o {out}")
                return 1
            log(f"• Render error, asking AI to fix (only the error is sent): {str(e)[:120]}")
            messages += [{"role": "assistant", "content": spec},
                         {"role": "user", "content": FIX_PROMPT.format(error=str(e)[:600])}]
            try:
                raw, usage = chat(system, messages, provider, model)
            except LLMError as e2:
                log(f"AI call failed: {e2}")
                return 1
            total = {k: total[k] + usage[k] for k in total}
            spec = clean(kind, raw)

    CACHE.mkdir(exist_ok=True)
    cache_file.write_text(spec, encoding="utf-8")
    log(f"✓ {kind} → {out}  ({time.time() - t:.1f}s render)")
    log(f"  spec → {spec_path}  (edit + re-render for free)")
    log(f"  tokens: {total['in']} in / {total['out']} out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
