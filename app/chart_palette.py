"""One colour per expense category for every chart and legend (QA #25).

Light colours are for the white page, dark ones for the dark card (``#1f2937``). Every category has its own hue; only
"other" is grey. ``tests/test_chart_palette.py`` keeps the set distinct and readable in both themes, and requires a
colour for each ``ExpenseCategory``.
"""

CATEGORY_COLORS_LIGHT = {
    "fuel": "#2563eb",
    "parts": "#ea580c",
    "service": "#16a34a",
    "insurance": "#7c3aed",
    "tax": "#dc2626",
    "fine": "#c026d3",
    "parking": "#0891b2",
    "toll": "#78350f",
    "washing": "#65a30d",
    "other": "#6b7280",
    "civil_liability": "#312e81",
    "casco": "#0f766e",
    "technical_inspection": "#b78103",
    "tachograph": "#db2777",
}

CATEGORY_COLORS_DARK = {
    "fuel": "#60a5fa",
    "parts": "#fb923c",
    "service": "#4ade80",
    "insurance": "#a78bfa",
    "tax": "#f87171",
    "fine": "#e879f9",
    "parking": "#22d3ee",
    "toll": "#d6b98c",
    "washing": "#a3e635",
    "other": "#9ca3af",
    "civil_liability": "#6366f1",
    "casco": "#2dd4bf",
    "technical_inspection": "#facc15",
    "tachograph": "#f472b6",
}

CHART_PALETTE = {"light": CATEGORY_COLORS_LIGHT, "dark": CATEGORY_COLORS_DARK}
