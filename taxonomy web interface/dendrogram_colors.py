r"""Extract per-topic colors and trial metadata from a Plotly dendrogram HTML.

Each `*_clustering_optimisation_*_trial_<N>.html` file contains a Plotly
figure embedded as `Plotly.newPlot("<id>", <DATA>, <LAYOUT>, <CONFIG>)`.
`<DATA>` is a JSON array of traces, each tracing one U-shape in the
dendrogram. The title text in `<LAYOUT>` carries the hyperparameters and
fit metrics, embedded as a JSON string with literal `<br>`
line breaks, e.g.:

    "Data point names, Trial 223<br>Params: n_neighbors=7,
     n_components=11, min_cluster_size=20, min_samples=15<br>
     Metrics: DBCV=0.527, CCC=0.508, Outlier Ratio=0.163, Clusters=35"

This module parses out:
    - `{topic_id (int) -> color (str)}` mapping for leaf topics, and
    - a `meta` dict with the hyperparameters and metrics.

The HTML files are ~5 MB each. Callers should cache the result.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_ID_RE = re.compile(r"^\s*(\d+)\s*:")

# Trial title regex. The HTML stores the title as a JSON string, so `<br>`
# tags appear as the literal escape sequence `<br>`. The pattern
# below matches both that and a raw `<br>` for safety.
_BR = r"(?:\\u003cbr\\u003e|<br>)"
# Some sources serialize the params/trial as floats (e.g. "Trial 203.0",
# "n_neighbors=19.0"); the GPT5-nano and Phi-4-mini combined plots use that.
_NUM = r"\d+(?:\.\d+)?"
_META_RE = re.compile(
    r"Trial\s+(" + _NUM + r")" + _BR +
    r"Params:\s*"
    r"n_neighbors=(" + _NUM + r"),\s*"
    r"n_components=(" + _NUM + r"),\s*"
    r"min_cluster_size=(" + _NUM + r"),\s*"
    r"min_samples=(" + _NUM + r")" + _BR +
    r"Metrics:\s*"
    r"DBCV=([-\d.]+),\s*"
    r"CCC=([-\d.]+),\s*"
    r"Outlier Ratio=([-\d.]+),\s*"
    r"Clusters=(\d+)"
)


def _bracket_balanced_slice(text: str, start: int) -> str | None:
    """Return the substring beginning at the `[` at `start` and ending at its
    matching `]`. Handles JSON-style strings (with escaped quotes). Returns
    None if no matching bracket is found.
    """
    if start < 0 or start >= len(text) or text[start] != "[":
        return None
    depth = 0
    in_str = False
    esc = False
    quote = ""
    i = start
    while i < len(text):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                in_str = False
        else:
            if c == '"' or c == "'":
                in_str = True
                quote = c
            elif c == "[" or c == "{":
                depth += 1
            elif c == "]" or c == "}":
                depth -= 1
                if depth == 0 and c == "]":
                    return text[start:i + 1]
        i += 1
    return None


def _colors_from_text(text: str) -> dict[int, str]:
    """Internal: parse the leaf-topic color map from already-loaded HTML text."""
    # The Plotly library bundle itself contains `Plotly.newPlot(`; the actual
    # figure render call is the last occurrence.
    i = text.rfind("Plotly.newPlot(")
    if i < 0:
        return {}
    comma = text.find(",", i)
    if comma < 0:
        return {}
    j = text.find("[", comma)
    data_text = _bracket_balanced_slice(text, j)
    if data_text is None:
        return {}
    try:
        data = json.loads(data_text)
    except json.JSONDecodeError:
        return {}

    colors: dict[int, str] = {}
    for trace in data:
        if not isinstance(trace, dict):
            continue
        marker = trace.get("marker") or {}
        color = marker.get("color")
        if not isinstance(color, str):
            continue
        for entry in trace.get("text") or []:
            if not entry:
                continue
            m = _ID_RE.match(str(entry))
            if not m:
                continue
            tid = int(m.group(1))
            colors.setdefault(tid, color)
    return colors


def _meta_from_text(text: str) -> dict:
    """Internal: parse hyperparameters + fit metrics from the title text."""
    m = _META_RE.search(text)
    if not m:
        return {}
    return {
        "trial": int(float(m.group(1))),
        "n_neighbors": int(float(m.group(2))),
        "n_components": int(float(m.group(3))),
        "min_cluster_size": int(float(m.group(4))),
        "min_samples": int(float(m.group(5))),
        "dbcv": float(m.group(6)),
        "ccc": float(m.group(7)),
        "outlier_ratio": float(m.group(8)),
        "clusters": int(m.group(9)),
    }


def extract_trial_info(html_path: Path) -> dict:
    """Parse the trial HTML once and return both colors and meta.

    Returns `{"colors": {topic_id: color}, "meta": {n_neighbors, ..., dbcv, ...}}`.
    Missing or unparseable HTML yields empty sub-dicts. Never raises.
    """
    if not html_path.is_file():
        return {"colors": {}, "meta": {}}
    try:
        text = html_path.read_text(encoding="utf-8")
    except OSError:
        return {"colors": {}, "meta": {}}
    return {"colors": _colors_from_text(text), "meta": _meta_from_text(text)}


def extract_topic_colors(html_path: Path) -> dict[int, str]:
    """Backwards-compatible wrapper returning only the color map."""
    return extract_trial_info(html_path).get("colors", {})
