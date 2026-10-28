"""Color themes. AI never writes CSS — all styling lives here and in templates/."""

THEMES = {
    "light": {
        "bg": "#ffffff", "fg": "#1f2937", "muted": "#6b7280",
        "accent": "#2563eb", "accent2": "#0ea5e9",
        "card": "#f3f6fb", "border": "#e5e7eb", "mermaid": "neutral",
    },
    "dark": {
        "bg": "#0f172a", "fg": "#e2e8f0", "muted": "#94a3b8",
        "accent": "#38bdf8", "accent2": "#a78bfa",
        "card": "#1e293b", "border": "#334155", "mermaid": "dark",
    },
    "forest": {
        "bg": "#fbfaf5", "fg": "#1c2b22", "muted": "#5b6b60",
        "accent": "#15803d", "accent2": "#ca8a04",
        "card": "#eef3ea", "border": "#d9e2d3", "mermaid": "forest",
    },
}

# Categorical colors for charts (readable on light and dark backgrounds)
PALETTE = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d"]


def css_vars(name: str) -> str:
    t = THEMES.get(name, THEMES["light"])
    return ":root{" + "".join(f"--{k}:{v};" for k, v in t.items() if k != "mermaid") + "}"
