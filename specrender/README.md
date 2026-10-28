# specrender

**AI writes a tiny spec → your machine renders the PDF / image.**

ChatGPT or Claude burn a lot of tokens making a PDF or image because they write the
styling, layout and rendering code every single time. Here the AI only writes the
*content* in a compact format. All the design lives in local templates, and the
rendering happens on your machine for free.

```
prompt / plan.md ──► LLM ──► small spec (Markdown / Mermaid / JSON) ──► local renderer ──► PDF / PNG / SVG
                     ~200–1500 tokens                                   0 tokens
```

| Type | AI writes | You get |
|---|---|---|
| `doc` | Markdown + frontmatter (mermaid blocks allowed) | A4 PDF with cover, tables, diagrams, page numbers |
| `diagram` | Mermaid code | PNG / SVG / PDF (flowchart, sequence, ER, class, mindmap, gantt…) |
| `poster` | Small JSON (title, stats, sections) | Poster PNG / PDF (portrait, square, story, landscape, A4) |
| `chart` | Small JSON (labels, series) | Bar / line / pie / donut as PNG / SVG / PDF |

Themes: `light`, `dark`, `forest` (edit `themes.py` to add your own).

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

Mermaid is bundled in `vendor/`, so diagrams render offline.

## Use

**Render a spec you already have (0 tokens):**

```bash
python render.py examples/plan.md                    # → out/plan.pdf
python render.py examples/login-flow.mmd             # → out/login-flow.png
python render.py examples/poster.json --theme dark
python render.py examples/chart.json -o out/chart.svg
```

**Let the AI write the spec:**

```bash
export ANTHROPIC_API_KEY=...      # or OPENAI_API_KEY / GEMINI_API_KEY, or run Ollama locally (free)

python render.py --ai -t diagram "TCP 3-way handshake"
python render.py --ai -t doc my-plan.md                  # a .md file works as the prompt
python render.py --ai -t poster "OSI model, one line per layer" --theme forest
python render.py --ai -t chart "Bangladesh population 2000-2020, every 5 years"
```

Output shows the token cost:

```
✓ diagram → out/tcp-3-way-handshake.png
  spec → out/tcp-3-way-handshake.spec.mmd  (edit + re-render for free)
  tokens: 180 in / 60 out
```

### Why it stays cheap

- **No styling from the AI.** Templates and themes are local.
- **Spec is saved.** Edit `out/*.spec.*` by hand and re-render with no AI call.
- **Cache.** The same prompt twice = 0 tokens (`--no-cache` to force).
- **Auto-fix sends only the error.** If Mermaid/JSON fails, the AI gets the error line, not your whole file (max `--fixes 2`).
- **Small models are enough.** Writing Mermaid or JSON is easy, so Haiku / gpt-4o-mini / Gemini Flash / a local Llama all work.

## Providers

| `--provider` | Key | Default model |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-haiku-5-5` |
| `openai` | `OPENAI_API_KEY` (+ `OPENAI_BASE_URL` for Groq, OpenRouter, etc.) | `gpt-4o-mini` |
| `gemini` | `GEMINI_API_KEY` | `gemini-2.5-flash` |
| `ollama` | none (local) | `llama3.2` |

Picked automatically from whichever key is set (Ollama if none). Change the model with
`--model` or `SPECRENDER_MODEL`. Model names change often, so check your provider's list if one fails.

## Spec formats

**doc** (`.md`)
```markdown
---
title: My Report
subtitle: One line
kicker: CSE Project
author: Name
theme: dark          # optional
---
## Section
Text, lists, tables, > notes, and ```mermaid blocks.
```

**diagram** (`.mmd`) — any Mermaid. Optional first line `%% title: ...` adds a heading.

**poster** (`.json`)
```json
{"kind":"poster","size":"portrait","tag":"Cheat sheet","title":"Git in *5 minutes*",
 "subtitle":"...","stats":[{"value":"6","label":"commands"}],
 "sections":[{"heading":"Start","points":["git clone <url>"]}],"footer":"...","handle":"@you"}
```
Wrap one word of the title in `*stars*` to color it.

**chart** (`.json`)
```json
{"kind":"chart","type":"bar","title":"...","labels":["Mon","Tue"],
 "series":[{"name":"A","values":[2,3]}],"unit":"h","note":"source"}
```
`type`: `bar`, `line`, `pie`, `donut`.

## Files

```
render.py      CLI
llm.py         provider calls + the short system prompts per type
renderers.py   spec → HTML/SVG → Playwright → PDF/PNG/SVG
themes.py      colors
templates/     doc, diagram, poster HTML templates
vendor/        mermaid.min.js (offline)
examples/      one spec of each type
```

## Limits

Code can draw documents, diagrams, charts, posters and infographics. It can't make
photos (people, scenery) — those still need an image model.

Bangla text works when the Hind Siliguri font loads (needs internet for Google Fonts),
or install *Noto Sans Bengali* locally for offline use.
