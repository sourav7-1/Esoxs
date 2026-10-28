"""Local renderers: compact spec  ->  PDF / PNG / SVG / HTML.
Zero tokens are spent here. Everything visual (layout, CSS, colors) lives in
templates/ and themes.py, so the AI only has to write content.
"""
from __future__ import annotations

import html
import json
import math
import re
from datetime import date
from pathlib import Path

import markdown as md
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

from themes import PALETTE, THEMES, css_vars

ROOT = Path(__file__).resolve().parent
MERMAID_JS = ROOT / "vendor" / "mermaid.min.js"
BASE_CSS = (ROOT / "templates" / "base.css").read_text(encoding="utf-8")
JINJA = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))

KINDS = ("doc", "diagram", "poster", "chart")
DEFAULT_EXT = {"doc": ".pdf", "diagram": ".png", "poster": ".png", "chart": ".png"}
POSTER_SIZES = {"portrait": (1080, 1350), "square": (1080, 1080), "story": (1080, 1920),
                "landscape": (1600, 900), "a4": (1240, 1754)}


class RenderError(Exception):
    """Spec could not be rendered. The message is short enough to send back to the AI for a fix."""


# ----------------------------------------------------------------------------- helpers

def infer_kind(path: Path, text: str) -> str:
    ext = path.suffix.lower()
    if ext in (".md", ".markdown", ".txt"):
        return "doc"
    if ext in (".mmd", ".mermaid"):
        return "diagram"
    if ext == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise RenderError(f"Invalid JSON: {e}")
        if data.get("kind") in KINDS:
            return data["kind"]
        return "chart" if "series" in data or "values" in data else "poster"
    raise RenderError(f"Can't guess the type from '{ext}'. Pass --type doc|diagram|poster|chart")


def parse_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RenderError(f"Invalid JSON: {e}")
    if not isinstance(data, dict):
        raise RenderError("JSON must be an object")
    return data


def split_frontmatter(text: str) -> tuple[dict, str]:
    meta: dict = {}
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip().strip('"').strip("'")
        text = text[m.end():]
    return meta, text


def _browser_render(page_html: str, out: Path, *, theme: str, width: int = 1200, height: int = 800,
                    selector: str | None = None, mermaid: bool = False, pdf_size: tuple | None = None,
                    pdf_footer: bool = False) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix.lower()
    if ext == ".html" and not mermaid:
        out.write_text(page_html, encoding="utf-8")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=2)
            page.set_default_timeout(30000)
            page.set_content(page_html, wait_until="load")
            if mermaid:
                page.add_script_tag(path=str(MERMAID_JS))
                err = page.evaluate(
                    """async (theme) => {
                        try {
                          mermaid.initialize({startOnLoad:false, theme, securityLevel:'strict',
                                              flowchart:{htmlLabels:false, curve:'basis'}});
                          await mermaid.run({querySelector:'pre.mermaid'});
                          return null;
                        } catch (e) { return String((e && e.message) || e); }
                    }""",
                    THEMES.get(theme, THEMES["light"])["mermaid"],
                )
                if err:
                    raise RenderError("Mermaid error: " + err.strip()[:600])
            page.evaluate("document.fonts.ready.then(() => true)")

            if ext == ".pdf":
                opts = dict(path=str(out), print_background=True)
                if pdf_size:  # posters / charts: one page exactly the size of the element
                    box = page.locator(selector).bounding_box() if selector else None
                    w, h = (box["width"], box["height"]) if box else pdf_size
                    opts.update(width=f"{math.ceil(w)}px", height=f"{math.ceil(h) + 1}px",
                                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
                else:  # documents: A4 with margins + page numbers
                    opts.update(format="A4", margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"})
                    if pdf_footer:
                        opts.update(display_header_footer=True, header_template="<span></span>",
                                    footer_template='<div style="width:100%;text-align:center;font-size:8px;color:#888">'
                                                    '<span class="pageNumber"></span> / <span class="totalPages"></span></div>')
                page.pdf(**opts)
            elif ext == ".png":
                if selector:
                    page.locator(selector).screenshot(path=str(out))
                else:
                    page.screenshot(path=str(out), full_page=True)
            elif ext == ".svg":
                svg = page.evaluate("() => { const s = document.querySelector('svg'); return s ? s.outerHTML : null }")
                if not svg:
                    raise RenderError("No SVG produced")
                out.write_text(svg, encoding="utf-8")
            elif ext == ".html":
                out.write_text(page.content(), encoding="utf-8")
            else:
                raise RenderError(f"Unsupported output format '{ext}' (use .pdf .png .svg .html)")
        finally:
            browser.close()


# ----------------------------------------------------------------------------- doc (Markdown -> PDF)

def render_doc(text: str, out: Path, theme: str = "light") -> None:
    meta, body = split_frontmatter(text)
    if meta.get("theme") in THEMES:
        theme = meta["theme"]
    if "date" not in meta:
        meta["date"] = date.today().strftime("%d %B %Y")
    if meta.get("date", "").lower() in ("none", "false", "-"):
        meta["date"] = ""

    # pull ```mermaid blocks out so Markdown doesn't touch them
    blocks: list[str] = []

    def stash(m):
        blocks.append(m.group(1).strip())
        return f"\n\nMERMAIDBLOCK{len(blocks) - 1}\n\n"

    body = re.sub(r"```mermaid\s*\n(.*?)```", stash, body, flags=re.S)

    # if there's no frontmatter title, promote the first "# Heading" to the cover
    if "title" not in meta:
        h1 = re.match(r"\s*#\s+(.+)\n", body)
        if h1:
            meta["title"] = h1.group(1).strip()
            body = body[h1.end():]

    body_html = md.markdown(body, extensions=["fenced_code", "tables", "sane_lists", "toc"])
    for i, code in enumerate(blocks):
        body_html = body_html.replace(f"<p>MERMAIDBLOCK{i}</p>", f'<pre class="mermaid">{html.escape(code)}</pre>')

    page = JINJA.get_template("doc.html").render(theme_css=css_vars(theme), base_css=BASE_CSS, meta=meta, body=body_html)
    _browser_render(page, out, theme=theme, width=794, mermaid=bool(blocks), pdf_footer=True)


# ----------------------------------------------------------------------------- diagram (Mermaid)

def render_diagram(code: str, out: Path, theme: str = "light", title: str | None = None) -> None:
    code = code.strip()
    if not code:
        raise RenderError("Empty Mermaid code")
    # optional "%% title: ..." first line
    m = re.match(r"%%\s*title:\s*(.+)\n", code)
    if m:
        title = title or m.group(1).strip()
    page = JINJA.get_template("diagram.html").render(theme_css=css_vars(theme), base_css=BASE_CSS, code=code, title=title)
    _browser_render(page, out, theme=theme, width=1400, selector=".wrap", mermaid=True, pdf_size=(1400, 900))


# ----------------------------------------------------------------------------- poster (JSON)

def render_poster(spec: dict, out: Path, theme: str = "light") -> None:
    if not spec.get("title"):
        raise RenderError("Poster JSON needs a 'title'")
    theme = spec.get("theme") if spec.get("theme") in THEMES else theme
    w, h = POSTER_SIZES.get(str(spec.get("size", "portrait")).lower(), POSTER_SIZES["portrait"])
    sections = [s for s in spec.get("sections", []) if isinstance(s, dict)]
    for s in sections:
        s.setdefault("heading", "")
        s["points"] = [str(p) for p in s.get("points", [])]
    title = str(spec["title"])
    # *word* in the title gets the accent color
    title_html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html.escape(title))
    plain_len = len(re.sub(r"\*", "", title))
    title_size = 96 if plain_len <= 22 else 80 if plain_len <= 40 else 64
    two_col = len(sections) >= 3 and w >= 1080 and sum(len(s["points"]) for s in sections) / max(len(sections), 1) <= 4
    page = JINJA.get_template("poster.html").render(
        theme_css=css_vars(theme), base_css=BASE_CSS, w=w, h=h, title_html=title_html, title_size=title_size,
        tag=spec.get("tag"), subtitle=spec.get("subtitle"), stats=spec.get("stats", [])[:4],
        sections=sections, two_col=two_col, footer=spec.get("footer"), handle=spec.get("handle"),
    )
    _browser_render(page, out, theme=theme, width=w, height=h, selector=".poster", pdf_size=(w, h))


# ----------------------------------------------------------------------------- chart (JSON -> SVG)

def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi == lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v < hi + step * 0.999:
        ticks.append(round(v, 10))
        v += step
    return ticks


def _fmt(v: float) -> str:
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f}M".replace(".0M", "M")
    if abs(v) >= 1e4:
        return f"{v / 1e3:.0f}k"
    return f"{v:g}" if v == int(v) else f"{v:.1f}"


def chart_svg(spec: dict, theme: str = "light") -> str:
    t = THEMES.get(spec.get("theme") or theme, THEMES["light"])
    ctype = str(spec.get("type", "bar")).lower()
    labels = [str(x) for x in spec.get("labels", [])]
    series = spec.get("series")
    if series is None and "values" in spec:
        series = [{"name": spec.get("name", ""), "values": spec["values"]}]
    if not labels or not series:
        raise RenderError("Chart JSON needs 'labels' and 'series' (or 'values')")
    for s in series:
        vals = s.get("values", [])
        if len(vals) != len(labels):
            raise RenderError(f"Series '{s.get('name', '')}' has {len(vals)} values but there are {len(labels)} labels")
        try:
            s["values"] = [float(v) for v in vals]
        except (TypeError, ValueError):
            raise RenderError(f"Series '{s.get('name', '')}' has non-numeric values")

    esc = html.escape
    W, H = 960, 560
    title, unit, note = spec.get("title", ""), spec.get("unit", ""), spec.get("note", "")
    font = 'font-family="Inter, Hind Siliguri, Segoe UI, sans-serif"'
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" {font}>',
             f'<rect width="{W}" height="{H}" fill="{t["bg"]}"/>']
    top = 40
    if title:
        parts.append(f'<text x="40" y="52" font-size="24" font-weight="700" fill="{t["fg"]}">{esc(title)}</text>')
        top = 72
    if note:
        parts.append(f'<text x="{W - 40}" y="{H - 14}" font-size="12" text-anchor="end" fill="{t["muted"]}">{esc(note)}</text>')

    multi = len(series) > 1
    if multi or ctype in ("pie", "donut"):
        items = series if ctype not in ("pie", "donut") else [{"name": l} for l in labels]
        x = 40 if ctype not in ("pie", "donut") else 600
        y = top + 10
        for i, s in enumerate(items):
            c = PALETTE[i % len(PALETTE)]
            if ctype in ("pie", "donut"):
                parts.append(f'<rect x="{x}" y="{y + i * 30}" width="14" height="14" rx="3" fill="{c}"/>'
                             f'<text x="{x + 22}" y="{y + i * 30 + 12}" font-size="15" fill="{t["fg"]}">{esc(s["name"])}</text>')
            else:
                parts.append(f'<rect x="{x}" y="{y}" width="14" height="14" rx="3" fill="{c}"/>'
                             f'<text x="{x + 22}" y="{y + 12}" font-size="14" fill="{t["fg"]}">{esc(str(s.get("name", "")))}</text>')
                x += 40 + 9 * len(str(s.get("name", "")))
        if ctype not in ("pie", "donut"):
            top += 34

    if ctype in ("pie", "donut"):
        vals = series[0]["values"]
        total = sum(v for v in vals if v > 0) or 1
        cx, cy, r = 300, (top + H) / 2, min(200, (H - top - 40) / 2)
        a0 = -math.pi / 2
        for i, v in enumerate(vals):
            if v <= 0:
                continue
            a1 = a0 + 2 * math.pi * v / total
            c = PALETTE[i % len(PALETTE)]
            if v >= total:
                parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{c}"/>')
            else:
                large = 1 if a1 - a0 > math.pi else 0
                x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
                x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
                parts.append(f'<path d="M{cx},{cy} L{x0:.2f},{y0:.2f} A{r},{r} 0 {large} 1 {x1:.2f},{y1:.2f} Z" '
                             f'fill="{c}" stroke="{t["bg"]}" stroke-width="2"/>')
            pct = 100 * v / total
            if pct >= 4:
                am = (a0 + a1) / 2
                lr = r * (0.78 if ctype == "donut" else 0.62)
                parts.append(f'<text x="{cx + lr * math.cos(am):.1f}" y="{cy + lr * math.sin(am) + 5:.1f}" font-size="15" '
                             f'font-weight="700" text-anchor="middle" fill="#fff">{pct:.0f}%</text>')
            a0 = a1
        if ctype == "donut":
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r * 0.55}" fill="{t["bg"]}"/>'
                         f'<text x="{cx}" y="{cy + 10}" font-size="28" font-weight="800" text-anchor="middle" '
                         f'fill="{t["fg"]}">{_fmt(total)}{esc(unit)}</text>')
        parts.append("</svg>")
        return "".join(parts)

    # axes for bar / line
    allv = [v for s in series for v in s["values"]]
    ticks = _nice_ticks(min(0, min(allv)), max(0, max(allv)))
    lo, hi = ticks[0], ticks[-1]
    rotate = len(labels) > 8 or max(len(l) for l in labels) > 12
    L, R, T, B = 80, W - 40, top + 10, H - (110 if rotate else 70)
    ys = lambda v: B - (v - lo) / (hi - lo) * (B - T)
    for tv in ticks:
        y = ys(tv)
        parts.append(f'<line x1="{L}" x2="{R}" y1="{y:.1f}" y2="{y:.1f}" stroke="{t["border"]}" '
                     f'stroke-width="{1.5 if tv == 0 else 1}"/>'
                     f'<text x="{L - 10}" y="{y + 5:.1f}" font-size="13" text-anchor="end" fill="{t["muted"]}">{_fmt(tv)}{esc(unit)}</text>')
    n = len(labels)
    band = (R - L) / n
    for i, lab in enumerate(labels):
        x = L + band * (i + 0.5)
        if rotate:
            parts.append(f'<text transform="translate({x:.1f},{B + 16}) rotate(-35)" font-size="13" text-anchor="end" '
                         f'fill="{t["fg"]}">{esc(lab[:24])}</text>')
        else:
            parts.append(f'<text x="{x:.1f}" y="{B + 24}" font-size="14" text-anchor="middle" fill="{t["fg"]}">{esc(lab)}</text>')

    show_vals = len(series) == 1 and n <= 12
    if ctype == "line":
        for si, s in enumerate(series):
            c = PALETTE[si % len(PALETTE)]
            pts = [(L + band * (i + 0.5), ys(v)) for i, v in enumerate(s["values"])]
            parts.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{c}" '
                         f'stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>')
            for (x, y), v in zip(pts, s["values"]):
                parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{t["bg"]}" stroke="{c}" stroke-width="3"/>')
                if show_vals:
                    parts.append(f'<text x="{x:.1f}" y="{y - 12:.1f}" font-size="13" text-anchor="middle" fill="{t["fg"]}">{_fmt(v)}</text>')
    else:  # bar
        k = len(series)
        bw = band * 0.7 / k
        for si, s in enumerate(series):
            c = PALETTE[si % len(PALETTE)]
            for i, v in enumerate(s["values"]):
                x = L + band * i + band * 0.15 + bw * si
                y0, y1 = ys(0), ys(v)
                parts.append(f'<rect x="{x:.1f}" y="{min(y0, y1):.1f}" width="{bw - 2:.1f}" height="{abs(y0 - y1):.1f}" rx="4" fill="{c}"/>')
                if show_vals:
                    parts.append(f'<text x="{x + bw / 2:.1f}" y="{min(y0, y1) - 8:.1f}" font-size="13" font-weight="600" '
                                 f'text-anchor="middle" fill="{t["fg"]}">{_fmt(v)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_chart(spec: dict, out: Path, theme: str = "light") -> None:
    svg = chart_svg(spec, theme)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".svg":
        out.write_text(svg, encoding="utf-8")
        return
    page = f'<!doctype html><html><head><meta charset="utf-8"><style>{BASE_CSS}body{{display:inline-block}}</style></head><body>{svg}</body></html>'
    _browser_render(page, out, theme=theme, width=960, height=560, selector="svg", pdf_size=(960, 560))


# ----------------------------------------------------------------------------- dispatcher

def render(kind: str, spec_text: str, out: Path, theme: str = "light") -> None:
    if kind == "doc":
        render_doc(spec_text, out, theme)
    elif kind == "diagram":
        render_diagram(spec_text, out, theme)
    elif kind == "poster":
        render_poster(parse_json(spec_text), out, theme)
    elif kind == "chart":
        render_chart(parse_json(spec_text), out, theme)
    else:
        raise RenderError(f"Unknown type '{kind}'")
