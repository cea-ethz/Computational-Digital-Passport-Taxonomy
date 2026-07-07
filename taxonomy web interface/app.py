r"""Flask backend for the expert-moderated taxonomy curator.

Reads automatically generated binary tree taxonomies from
`../generated topic hierarchies/` (read-only) and serves a two-pane UI where
the expert drags, renames, and restructures them into a single moderated
taxonomy. All writable state lives in `taxonomy web interface/workspace/`.

Run from the "taxonomy web interface/" directory:
    .\.venv\Scripts\python.exe app.py
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file, url_for

from catalogue import Catalogue, canonical_key, load_catalogue
from dendrogram_colors import extract_trial_info

# --- Paths --------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("TAXONOMY_DATA_ROOT", HERE.parent)).resolve()
RESULTS_ROOT = DATA_ROOT / "generated topic hierarchies"
WORKSPACE = HERE / "workspace"
STATIC = HERE / "static"
MODERATED_PATH = WORKSPACE / "moderated_taxonomy.json"
PLACED_PATH = WORKSPACE / "placed_keys.json"
CATALOGUE_PATH = DATA_ROOT / "data point descriptions" / "filtered_descriptions_Apertus-8B-Instruct-2509s"
VIEWER_TEMPLATE = STATIC / "viewer_template.html"
# The published "final" digital passport taxonomy, embedded as a `clusterData`
# JSON object inside its standalone viewer HTML. Exposed in the left-pane trial
# picker (source key "final") so the expert can drag from / seed a moderated
# tree off the reference taxonomy, with full data-point (placed) tracking.
FINAL_TAXONOMY_HTML = DATA_ROOT / "moderated digital passport taxonomy" / "251220 digital passport taxonomy viewer.html"
FINAL_SOURCE = "final"

# Blind qualitative evaluation: top 6 trials per source are shuffled once.
# Each LEAF topic of each trial is rated on two 1–5 axes — cohesion (DBCV-
# inspired: do the data points belong together?) and hierarchical fit (CCC-
# inspired: is this leaf in the right place under its parent?). The per-trial
# overall is the unweighted mean across leaves. Three workspace files:
#   evaluation_order.json      — shuffled (position, source, trial) list, written once.
#   evaluation_draft.json      — editable per-leaf scores, auto-saved on every click.
#   evaluation_submitted.json  — frozen ratings + derived per-trial means.
# To fully reset, delete all three. Delete only the order file to reshuffle while
# preserving prior scores (kept by source:trial). Delete only the submitted file
# to re-open editing.
EVAL_ORDER_PATH = WORKSPACE / "evaluation_order.json"
EVAL_DRAFT_PATH = WORKSPACE / "evaluation_draft.json"
EVAL_SUBMITTED_PATH = WORKSPACE / "evaluation_submitted.json"
EVAL_TOP_N = 4

# Persisted result of `top_n_per_source()`. The selection requires parsing every
# trial's ~5 MB combined-plot HTML and reading every trial JSON for the leaf
# signature, which adds visible latency to the first /api/trials after a server
# restart. The data tree is read-only so the answer is stable — we cache the
# full list to disk and reuse it on every subsequent boot. Delete this file to
# refresh after adding/removing trials under generated topic hierarchies/.
TOP_TRIALS_PATH = WORKSPACE / "top_trials.json"

# Manual-stopwatch timers for the evaluation and moderation pages. The schema
# is identical (moderation just also uses `finished_at`):
#   { "elapsed_seconds": float, "running": bool, "running_since": iso|null,
#     "finished_at": iso|null }
# Live total = elapsed_seconds + (now - running_since) when running.
EVAL_TIMER_PATH = WORKSPACE / "evaluation_timer.json"
MOD_TIMER_PATH = WORKSPACE / "moderation_timer.json"

# Canonical source order for the trial picker and the evaluation cut.
SOURCE_ORDER = ["apertus", "data_points", "gpt5_nano", "phi4_mini"]

WORKSPACE.mkdir(exist_ok=True)

# Lazy-loaded catalogue
_CATALOGUE: Catalogue | None = None

# Per-trial parsed HTML data (colors + metadata). The HTML files are ~5 MB so
# we parse each one at most once per server process.
_TRIAL_INFO: dict[tuple[str, int], dict] = {}


def get_trial_info(source: str, trial: int) -> dict:
    """Return `{"colors": {topic_id: color}, "meta": {...}}` for the trial."""
    key = (source, trial)
    cached = _TRIAL_INFO.get(key)
    if cached is not None:
        return cached
    html_path = html_path_for_trial(source, trial)
    info = extract_trial_info(html_path) if html_path else {"colors": {}, "meta": {}}
    _TRIAL_INFO[key] = info
    return info


def get_trial_colors(source: str, trial: int) -> dict[int, str]:
    return get_trial_info(source, trial).get("colors", {})


def get_trial_meta(source: str, trial: int) -> dict:
    return get_trial_info(source, trial).get("meta", {})


def get_catalogue() -> Catalogue:
    global _CATALOGUE
    if _CATALOGUE is None:
        if not CATALOGUE_PATH.is_dir():
            abort(500, description=f"catalogue folder not found at {CATALOGUE_PATH}")
        _CATALOGUE = load_catalogue(CATALOGUE_PATH)
    return _CATALOGUE


# --- Trial discovery ----------------------------------------------------------

@dataclass(frozen=True)
class TrialSource:
    key: str          # short id used in URLs
    folder: str       # subfolder under RESULTS_ROOT
    label: str        # human label
    pattern: re.Pattern[str]  # filename pattern, must capture group 1 = trial number


SOURCES: dict[str, TrialSource] = {
    "apertus": TrialSource(
        key="apertus",
        folder="Apertus-8B-Instruct",
        label="Apertus-8B-Instruct",
        pattern=re.compile(r"^.*_Apertus-8B-Instruct_trial_(\d+)\.json$"),
    ),
    "data_points": TrialSource(
        key="data_points",
        folder="data_points",
        label="data point names",
        pattern=re.compile(r"^.*_data_point_names_trial_(\d+)\.json$"),
    ),
    "gpt5_nano": TrialSource(
        key="gpt5_nano",
        folder="GPT5-nano",
        label="GPT5-nano",
        pattern=re.compile(r"^.*_GPT5-nano_grouped_datapoints_trial_(\d+)_Phi-4-mini\.json$"),
    ),
    "phi4_mini": TrialSource(
        key="phi4_mini",
        folder="Phi-4-mini",
        # The Phi-4-mini folder also contains an older `Phi-4-mini_grouped_…`
        # set with overlapping trial numbers but different content; use the
        # 14-trial canonical set that matches the combined-plot HTMLs.
        label="Phi-4-mini",
        pattern=re.compile(r"^251218_grouped_datapoints_trial_(\d+)_Phi-4-mini\.json$"),
    ),
}

# Sources whose HTML files are combined-plot dashboards (one file per trial
# but the filename does not embed the trial number). Trial -> HTML mapping is
# built lazily by parsing the title of each HTML.
_COMBINED_PLOT_SOURCES = {"gpt5_nano", "phi4_mini"}
_COMBINED_PLOT_GLOB = "*combined_plot_*_references.html"
_COMBINED_PLOT_MAP: dict[str, dict[int, Path]] = {}


def _combined_plot_map(source_key: str) -> dict[int, Path]:
    cached = _COMBINED_PLOT_MAP.get(source_key)
    if cached is not None:
        return cached
    mapping: dict[int, Path] = {}
    src = SOURCES.get(source_key)
    if src is not None:
        folder = RESULTS_ROOT / src.folder
        if folder.is_dir():
            title_re = re.compile(r"Trial\s+(\d+(?:\.\d+)?)")
            for f in sorted(folder.glob(_COMBINED_PLOT_GLOB)):
                try:
                    text = f.read_text(encoding="utf-8")
                except OSError:
                    continue
                m = title_re.search(text)
                if m:
                    tid = int(float(m.group(1)))
                    mapping.setdefault(tid, f)
    _COMBINED_PLOT_MAP[source_key] = mapping
    return mapping


def html_path_for_trial(source_key: str, trial_num: int) -> Path | None:
    """Locate the HTML companion file for a (source, trial) pair."""
    if source_key in _COMBINED_PLOT_SOURCES:
        return _combined_plot_map(source_key).get(trial_num)
    try:
        return trial_path(source_key, trial_num).with_suffix(".html")
    except Exception:
        return None


def list_trials() -> list[dict[str, Any]]:
    """Return all available trials across known source folders."""
    out: list[dict[str, Any]] = []
    for src in SOURCES.values():
        folder = RESULTS_ROOT / src.folder
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.json")):
            m = src.pattern.match(f.name)
            if not m:
                continue
            out.append({
                "source": src.key,
                "source_label": src.label,
                "trial": int(m.group(1)),
                "filename": f.name,
            })
    out.sort(key=lambda x: (x["source"], x["trial"]))
    return out


def trial_path(source_key: str, trial: int) -> Path:
    if source_key not in SOURCES:
        abort(404, description=f"unknown source: {source_key}")
    src = SOURCES[source_key]
    folder = RESULTS_ROOT / src.folder
    matches = [f for f in folder.glob("*.json") if src.pattern.match(f.name) and int(src.pattern.match(f.name).group(1)) == trial]
    if not matches:
        abort(404, description=f"trial {trial} not found in {src.folder}")
    return matches[0]


# --- Schema normalization -----------------------------------------------------

def normalize_node(node: dict[str, Any]) -> dict[str, Any]:
    """Convert a trial-JSON node (`0_topic_id`/`1_topic_name`/`2_children`/`2_data_points`)
    into the internal/viewer schema (`topic_id`/`topic_name`/`children`/`data_points`).
    Passes through nodes that are already in the internal schema."""
    out: dict[str, Any] = {}
    out["topic_id"] = node.get("topic_id", node.get("0_topic_id"))
    out["topic_name"] = node.get("topic_name", node.get("1_topic_name", ""))
    children = node.get("children", node.get("2_children"))
    data_points = node.get("data_points", node.get("2_data_points"))
    if children:
        out["children"] = [normalize_node(c) for c in children]
    if data_points:
        out["data_points"] = list(data_points)
    return out


def load_final_taxonomy() -> dict[str, Any]:
    """Parse the embedded `const clusterData = {...}` object from the final
    taxonomy viewer HTML and return it in the internal/viewer schema.

    The published tree reuses `topic_id` values across nodes (e.g. 93 appears
    on several topics), which would collide in the editor's per-node keying for
    expand/collapse, drag and dp-list state. We therefore reassign a unique,
    stable id to every node after normalization."""
    text = FINAL_TAXONOMY_HTML.read_text(encoding="utf-8")
    marker = text.find("const clusterData")
    if marker == -1:
        abort(500, description="could not find clusterData in final taxonomy HTML")
    start = text.index("{", marker)
    depth = 0
    end = None
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        abort(500, description="unbalanced braces parsing clusterData")
    raw = json.loads(text[start:end])
    node = normalize_node(raw)
    counter = [0]

    def reassign_ids(n: dict[str, Any]) -> None:
        counter[0] += 1
        n["topic_id"] = f"final_{counter[0]}"
        for c in n.get("children", []) or []:
            reassign_ids(c)

    reassign_ids(node)
    return node


def load_trial_taxonomy(source: str, trial: int) -> dict[str, Any]:
    """Normalized taxonomy for a trial, or the reference tree when source is
    the synthetic FINAL_SOURCE. Centralizes the final-vs-trial branch so the
    trial-view and init-moderated endpoints stay in sync."""
    if source == FINAL_SOURCE:
        return load_final_taxonomy()
    path = trial_path(source, trial)
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return normalize_node(raw)


def walk_data_points(node: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if isinstance(node.get("data_points"), list):
        out.extend(node["data_points"])
    for c in node.get("children", []) or []:
        out.extend(walk_data_points(c))
    return out


def walk_canonical_keys(node: dict[str, Any]) -> list[str]:
    return [canonical_key(dp) for dp in walk_data_points(node)]


def walk_leaf_topic_ids(node: dict[str, Any]) -> list[str]:
    """Stringified topic_ids of every leaf node (one that carries data points).
    Used by per-topic evaluation to know which topics the rater must score."""
    out: list[str] = []
    if isinstance(node.get("data_points"), list) and node["data_points"]:
        tid = node.get("topic_id")
        if tid is not None:
            out.append(str(tid))
    for c in node.get("children", []) or []:
        out.extend(walk_leaf_topic_ids(c))
    return out


def dedupe_node_in_place(node: dict[str, Any], seen: set[str] | None = None) -> dict[str, Any]:
    """Walk the tree depth-first and drop any data point whose canonical key
    was already seen earlier in the traversal. Empty leaves and empty branches
    are pruned. Returns the (possibly mutated) node.
    """
    if seen is None:
        seen = set()
    if isinstance(node.get("data_points"), list):
        kept = []
        for dp in node["data_points"]:
            k = canonical_key(dp)
            if k in seen:
                continue
            seen.add(k)
            kept.append(dp)
        if kept:
            node["data_points"] = kept
        else:
            node.pop("data_points", None)
    if isinstance(node.get("children"), list):
        new_children = []
        for c in node["children"]:
            dedupe_node_in_place(c, seen)
            has_dps = bool(c.get("data_points"))
            has_kids = bool(c.get("children"))
            if has_dps or has_kids:
                new_children.append(c)
        if new_children:
            node["children"] = new_children
        else:
            node.pop("children", None)
    return node


# --- Workspace I/O ------------------------------------------------------------

def atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically: write to a temp file in the same dir, then replace.

    On Windows, antivirus / Search indexer / cloud-sync agents briefly hold a
    read handle on the destination right after a previous write, which races
    `os.replace` and surfaces as WinError 5 / PermissionError. A short
    exponential backoff (~640 ms total) absorbs those transient locks without
    the caller seeing a 500."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        delay = 0.01
        for attempt in range(7):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 6:
                    raise
                time.sleep(delay)
                delay *= 2
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def load_moderated() -> dict[str, Any] | None:
    if not MODERATED_PATH.exists():
        return None
    with open(MODERATED_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_placed() -> set[str]:
    """Load the set of placed canonical keys from disk. If the keys file is
    missing but a moderated taxonomy exists (e.g., after a schema migration),
    derive the keys from the taxonomy and write them back."""
    if PLACED_PATH.exists():
        with open(PLACED_PATH, "r", encoding="utf-8") as fh:
            return set(json.load(fh))
    if MODERATED_PATH.exists():
        with open(MODERATED_PATH, "r", encoding="utf-8") as fh:
            tax = json.load(fh)
        placed = set(walk_canonical_keys(tax))
        atomic_write_json(PLACED_PATH, sorted(placed))
        return placed
    return set()


def save_moderated(taxonomy: dict[str, Any]) -> set[str]:
    """Save taxonomy + recompute the placed canonical-key set. Returns the new set."""
    placed = set(walk_canonical_keys(taxonomy))
    atomic_write_json(MODERATED_PATH, taxonomy)
    atomic_write_json(PLACED_PATH, sorted(placed))
    return placed


# --- Manual stopwatch timers --------------------------------------------------

def _timer_default() -> dict[str, Any]:
    return {"elapsed_seconds": 0.0, "running": False, "running_since": None, "finished_at": None}


def load_timer(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _timer_default()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError:
        app.logger.warning("corrupt %s; treating as fresh", path.name)
        return _timer_default()
    if not isinstance(data, dict):
        return _timer_default()
    out = _timer_default()
    if isinstance(data.get("elapsed_seconds"), (int, float)):
        out["elapsed_seconds"] = float(data["elapsed_seconds"])
    out["running"] = bool(data.get("running"))
    rs = data.get("running_since")
    out["running_since"] = rs if isinstance(rs, str) else None
    fa = data.get("finished_at")
    out["finished_at"] = fa if isinstance(fa, str) else None
    # A running flag with no timestamp would silently lose time; clear it.
    if out["running"] and not out["running_since"]:
        out["running"] = False
    return out


def save_timer(path: Path, state: dict[str, Any]) -> None:
    atomic_write_json(path, state)


def _parse_iso(s: str) -> datetime:
    # fromisoformat handles the format we emit via _utc_iso (offset-aware).
    return datetime.fromisoformat(s)


def current_total_seconds(state: dict[str, Any]) -> float:
    total = float(state.get("elapsed_seconds") or 0.0)
    if state.get("running") and state.get("running_since"):
        try:
            running_since = _parse_iso(state["running_since"])
            delta = (datetime.now(timezone.utc) - running_since).total_seconds()
            if delta > 0:
                total += delta
        except ValueError:
            pass
    return total


def _fold_running_into_elapsed(state: dict[str, Any]) -> None:
    """Move any in-flight running interval into elapsed_seconds, then pause."""
    state["elapsed_seconds"] = current_total_seconds(state)
    state["running"] = False
    state["running_since"] = None


def timer_action(path: Path, action: str, *, allow_finish: bool = False) -> dict[str, Any]:
    """Apply start | stop | finish | reopen and persist the new state.
    `allow_finish` gates the moderation-only actions (finish, reopen)."""
    state = load_timer(path)
    if action == "start":
        # An implicit reopen on the moderation page: clicking Start while
        # `finished_at` is set clears the completion marker.
        if allow_finish and state.get("finished_at"):
            state["finished_at"] = None
        if not state["running"]:
            state["running"] = True
            state["running_since"] = _utc_iso()
    elif action == "stop":
        if state["running"]:
            _fold_running_into_elapsed(state)
    elif action == "finish" and allow_finish:
        if state["running"]:
            _fold_running_into_elapsed(state)
        state["finished_at"] = _utc_iso()
    elif action == "reopen" and allow_finish:
        state["finished_at"] = None
    else:
        abort(400, description=f"unknown timer action: {action!r}")
    save_timer(path, state)
    return state


def _timer_response(state: dict[str, Any]) -> Any:
    return jsonify({
        "ok": True,
        "state": state,
        "current_total_seconds": current_total_seconds(state),
    })


# --- Flask app ----------------------------------------------------------------

app = Flask(__name__, template_folder=str(HERE / "templates"), static_folder=str(STATIC))


@app.route("/")
def index():
    # First-time users are routed to the blind evaluation; once every leaf
    # has been rated (draft complete) or the evaluation has been submitted,
    # "/" hands them off to the moderation workbench.
    if load_evaluation_submitted() is not None:
        return redirect(url_for("moderation_page"))
    order = load_or_create_evaluation_order()
    if is_draft_complete(order, load_evaluation_draft()):
        return redirect(url_for("moderation_page"))
    return redirect(url_for("evaluation_page"))


@app.route("/moderation")
def moderation_page():
    return render_template("index.html")


@app.route("/api/trials")
def api_trials():
    """Top 6 trials per source by avg(DBCV, CCC) desc, in canonical
    SOURCE_ORDER. Each item carries the user's submitted rating (or null)
    so the moderation page can surface and sort by it."""
    user_ratings = load_user_ratings_for_picker()
    out: list[dict[str, Any]] = []
    # Reference tree first, so it sits at the top of the picker. Carries no
    # DBCV/CCC/rating — the front-end picker renders those as "—".
    if FINAL_TAXONOMY_HTML.exists():
        out.append({
            "source": FINAL_SOURCE,
            "source_label": "Final taxonomy (reference)",
            "trial": 0,
            "filename": FINAL_TAXONOMY_HTML.name,
            "meta": {},
            "group_rank": 0,
            "user_rating": None,
        })
    for t in top_n_per_source():
        out.append({
            "source": t["source"],
            "source_label": t["source_label"],
            "trial": t["trial"],
            "filename": t["filename"],
            "meta": t["meta"],
            "group_rank": t["group_rank"],
            "user_rating": user_ratings.get(f"{t['source']}:{t['trial']}"),
        })
    return jsonify(out)


@app.route("/api/trial/<source>/<int:trial>")
def api_trial(source: str, trial: int):
    normalized = load_trial_taxonomy(source, trial)
    # Append a synthetic leaf containing every catalogue data point that this
    # trial's clustering did not include. Lets the expert reach the full
    # 1909-entry catalogue from any trial view. Greying (the placed set)
    # still applies to its data points individually.
    trial_keys = set(walk_canonical_keys(normalized))
    cat = get_catalogue()
    missing = [e for e in cat.entries if e.key not in trial_keys]
    if missing:
        synth = {
            "topic_id": "_missing",
            "topic_name": f"(not in this tree — {len(missing)} data points)",
            "data_points": [f"{e.text} ({e.file})" for e in missing],
            "_synthetic": True,
        }
        if isinstance(normalized.get("children"), list):
            normalized["children"].append(synth)
        else:
            normalized["children"] = [synth]
    placed = load_placed()
    # The reference tree has no per-trial dendrogram colors / clustering metrics.
    info = {"colors": {}, "meta": {}} if source == FINAL_SOURCE else get_trial_info(source, trial)
    return jsonify({
        "taxonomy": normalized,
        "placed": sorted(placed),
        "topic_colors": {str(k): v for k, v in info.get("colors", {}).items()},
        "meta": info.get("meta", {}),
    })


@app.route("/api/moderated", methods=["GET"])
def api_get_moderated():
    return jsonify({"taxonomy": load_moderated(), "placed": sorted(load_placed())})


@app.route("/api/moderated", methods=["POST"])
def api_save_moderated():
    body = request.get_json(force=True, silent=False)
    taxonomy = body.get("taxonomy") if isinstance(body, dict) else None
    if not isinstance(taxonomy, dict):
        abort(400, description="body must be {\"taxonomy\": <node>}")
    placed = save_moderated(taxonomy)
    return jsonify({"ok": True, "placed": sorted(placed)})


@app.route("/api/init-moderated", methods=["POST"])
def api_init_moderated():
    body = request.get_json(force=True, silent=False) or {}
    source = body.get("source")
    trial = body.get("trial")
    if not source or trial is None:
        abort(400, description="body must include {source, trial}")
    taxonomy = load_trial_taxonomy(source, int(trial))
    # Dedupe by canonical key: a data point appearing multiple times in the
    # seed trial is collapsed to its first occurrence (some Apertus trees
    # contain 5-10 such duplicates).
    dedupe_node_in_place(taxonomy)
    placed = save_moderated(taxonomy)
    return jsonify({"ok": True, "taxonomy": taxonomy, "placed": sorted(placed)})


@app.route("/api/catalogue")
def api_catalogue():
    cat = get_catalogue()
    return jsonify({
        "size": len(cat),
        "path": str(CATALOGUE_PATH),
    })


@app.route("/api/reset", methods=["POST"])
def api_reset():
    for p in (MODERATED_PATH, PLACED_PATH, MOD_TIMER_PATH):
        if p.exists():
            p.unlink()
    return jsonify({"ok": True})


@app.route("/api/export/json")
def api_export_json():
    tax = load_moderated()
    if tax is None:
        abort(409, description="no moderated taxonomy yet")
    payload = json.dumps(tax, ensure_ascii=False, indent=2)
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=moderated_taxonomy.json"},
    )


@app.route("/api/export/html")
def api_export_html():
    tax = load_moderated()
    if tax is None:
        abort(409, description="no moderated taxonomy yet")
    if not VIEWER_TEMPLATE.exists():
        abort(500, description=f"viewer template missing at {VIEWER_TEMPLATE}")
    template = VIEWER_TEMPLATE.read_text(encoding="utf-8")
    if "__CLUSTER_DATA__" not in template:
        abort(500, description="viewer template is missing the __CLUSTER_DATA__ marker")
    embedded = json.dumps(tax, ensure_ascii=False)
    html = template.replace("__CLUSTER_DATA__", embedded)
    return Response(
        html,
        mimetype="text/html",
        headers={"Content-Disposition": "attachment; filename=moderated_taxonomy_viewer.html"},
    )


# --- Blind qualitative evaluation ---------------------------------------------
#
# Per-topic scoring. Inspired by:
#   * DBCV — density-based per-cluster validation → COHESION ("do these data
#     points belong together?"), rated per leaf on a 1–5 scale.
#   * CCC — cophenetic correlation, structure preservation → HIERARCHICAL FIT
#     ("is this leaf in the right place under its parent?"), per leaf, 1–5.
# Per-trial overall = unweighted mean across leaves.

FIT_SCORE_MIN = 1
FIT_SCORE_MAX = 5
# Cohesion is rated by percentage bucket — the rater estimates what fraction
# of the data points in this leaf genuinely belong together. Stored as the
# bucket midpoint (integers) so the picker / summary can present a real %.
COHESION_BUCKETS = (10, 30, 50, 70, 90)

# Per-trial leaf topic_ids are derived once from the trial JSON and cached for
# the life of the server process (trial files are read-only).
_TRIAL_LEAF_IDS: dict[tuple[str, int], list[str]] = {}


def trial_leaf_ids(source: str, trial: int) -> list[str]:
    key = (source, trial)
    cached = _TRIAL_LEAF_IDS.get(key)
    if cached is not None:
        return cached
    with open(trial_path(source, trial), "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    ids = walk_leaf_topic_ids(normalize_node(raw))
    _TRIAL_LEAF_IDS[key] = ids
    return ids


# Per-trial canonical clustering signature, used to dedupe within a source.
# Two trials with the same signature partition the catalogue into the same
# leaves (identical sets of canonical data-point keys per leaf), regardless of
# topic names or hierarchy. Cached for the life of the server process.
_TRIAL_LEAF_SIG: dict[tuple[str, int], frozenset[frozenset[str]]] = {}


def trial_leaf_signature(source: str, trial: int) -> frozenset[frozenset[str]]:
    key = (source, trial)
    cached = _TRIAL_LEAF_SIG.get(key)
    if cached is not None:
        return cached
    with open(trial_path(source, trial), "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    norm = normalize_node(raw)
    leaves: list[frozenset[str]] = []

    def walk(n: dict[str, Any]) -> None:
        if isinstance(n.get("data_points"), list) and n["data_points"]:
            leaves.append(frozenset(canonical_key(dp) for dp in n["data_points"]))
        for c in n.get("children", []) or []:
            walk(c)

    walk(norm)
    sig = frozenset(leaves)
    _TRIAL_LEAF_SIG[key] = sig
    return sig


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def top_n_per_source(n: int = EVAL_TOP_N) -> list[dict[str, Any]]:
    """Top N DISTINCT trials per source by avg(DBCV, CCC) desc. Cached to
    `TOP_TRIALS_PATH` so the expensive selection (HTML meta parsing + leaf
    signature scans across every trial) only happens once per source-code
    snapshot. Pass a non-default `n` to bypass the cache."""
    if n == EVAL_TOP_N and TOP_TRIALS_PATH.exists():
        try:
            with open(TOP_TRIALS_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list) and all(isinstance(x, dict) for x in data):
                return data
        except json.JSONDecodeError:
            app.logger.warning("corrupt %s; regenerating", TOP_TRIALS_PATH.name)
    items = _compute_top_n_per_source(n)
    if n == EVAL_TOP_N:
        atomic_write_json(TOP_TRIALS_PATH, items)
    return items


def _compute_top_n_per_source(n: int = EVAL_TOP_N) -> list[dict[str, Any]]:
    """Compute the top N DISTINCT trials per source by avg(DBCV, CCC) desc.
    Trials with the same clustering signature (identical leaf partition by
    canonical data point key) are deduplicated within a source — only the
    highest-scoring representative survives — so the rater never sees the
    same partition twice. Trials with missing DBCV or CCC are skipped."""
    all_trials = list_trials()
    by_source: dict[str, list[dict[str, Any]]] = {}
    for t in all_trials:
        meta = get_trial_meta(t["source"], t["trial"])
        dbcv = meta.get("dbcv")
        ccc = meta.get("ccc")
        if not isinstance(dbcv, (int, float)) or not isinstance(ccc, (int, float)):
            continue
        avg = (dbcv + ccc) / 2.0
        by_source.setdefault(t["source"], []).append({
            "source": t["source"],
            "source_label": t["source_label"],
            "trial": t["trial"],
            "filename": t["filename"],
            "dbcv": float(dbcv),
            "ccc": float(ccc),
            "avg": avg,
            "meta": meta,
        })
    out: list[dict[str, Any]] = []
    keys = [k for k in SOURCE_ORDER if k in by_source] + [k for k in by_source if k not in SOURCE_ORDER]
    for k in keys:
        sorted_items = sorted(by_source[k], key=lambda x: x["avg"], reverse=True)
        kept: list[dict[str, Any]] = []
        seen_sigs: set[frozenset[frozenset[str]]] = set()
        for item in sorted_items:
            sig = trial_leaf_signature(item["source"], item["trial"])
            if sig in seen_sigs:
                continue
            seen_sigs.add(sig)
            kept.append(item)
            if len(kept) == n:
                break
        for rank, item in enumerate(kept, start=1):
            item["group_rank"] = rank
            out.append(item)
    return out


def _is_old_evaluation_schema(data: dict[str, Any]) -> bool:
    """True if any per-trial entry uses an incompatible legacy shape and
    should be discarded. Covers two historical schemas:
      * `{hierarchy, grouping}` per-trial (the original 1–10 slider design).
      * `{topics: {id: {cohesion: 1..5}}}` (the interim 1–5 design, before
        cohesion was switched to percentage buckets).
    Disk files are left untouched; we simply ignore them on load."""
    ratings = data.get("ratings") if "ratings" in data else data
    if not isinstance(ratings, dict):
        return False
    for v in ratings.values():
        if not isinstance(v, dict):
            continue
        if "topics" not in v and ("hierarchy" in v or "grouping" in v):
            return True
        topics = v.get("topics")
        if isinstance(topics, dict):
            for t in topics.values():
                if not isinstance(t, dict):
                    continue
                c = t.get("cohesion")
                if isinstance(c, int) and c not in COHESION_BUCKETS:
                    return True
    return False


def load_evaluation_draft() -> dict[str, Any]:
    if not EVAL_DRAFT_PATH.exists():
        return {}
    try:
        with open(EVAL_DRAFT_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError:
        app.logger.warning("corrupt %s; treating as empty", EVAL_DRAFT_PATH.name)
        return {}
    if not isinstance(data, dict):
        return {}
    if _is_old_evaluation_schema(data):
        app.logger.warning(
            "%s uses the legacy hierarchy/grouping schema; ignoring",
            EVAL_DRAFT_PATH.name,
        )
        return {}
    return data


def save_evaluation_draft(draft: dict[str, Any]) -> None:
    atomic_write_json(EVAL_DRAFT_PATH, draft)


def load_evaluation_submitted() -> dict[str, Any] | None:
    if not EVAL_SUBMITTED_PATH.exists():
        return None
    try:
        with open(EVAL_SUBMITTED_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError:
        app.logger.warning("corrupt %s; treating as missing", EVAL_SUBMITTED_PATH.name)
        return None
    if not isinstance(data, dict):
        return None
    if _is_old_evaluation_schema(data):
        app.logger.warning(
            "%s uses the legacy hierarchy/grouping schema; ignoring",
            EVAL_SUBMITTED_PATH.name,
        )
        return None
    return data


def save_evaluation_submitted(payload: dict[str, Any]) -> None:
    atomic_write_json(EVAL_SUBMITTED_PATH, payload)


def _trial_means(topics: dict[str, Any]) -> tuple[float, float, float] | None:
    """Mean cohesion (0–100 %), mean fit (1–5), and a composite overall on a
    0–100 % scale (fit normalised by /5 then averaged with cohesion). Returns
    None if either dimension has no ratings yet."""
    cohs: list[int] = []
    fits: list[int] = []
    for t in topics.values():
        if not isinstance(t, dict):
            continue
        c = t.get("cohesion")
        f = t.get("fit")
        if isinstance(c, int):
            cohs.append(c)
        if isinstance(f, int):
            fits.append(f)
    if not cohs or not fits:
        return None
    cohesion_mean = sum(cohs) / len(cohs)
    fit_mean = sum(fits) / len(fits)
    overall_pct = (cohesion_mean + (fit_mean / FIT_SCORE_MAX) * 100.0) / 2.0
    return cohesion_mean, fit_mean, overall_pct


def load_user_ratings_for_picker() -> dict[str, dict[str, float]]:
    """Return `{"<source>:<trial>": {cohesion_mean, fit_mean, avg}}` from the
    submitted file, or empty if no evaluation has been submitted yet.
    cohesion_mean and avg are percentages (0–100); fit_mean is 1–5."""
    submitted = load_evaluation_submitted()
    if not submitted:
        return {}
    ratings = submitted.get("ratings") or {}
    out: dict[str, dict[str, float]] = {}
    for key, r in ratings.items():
        if not isinstance(r, dict):
            continue
        topics = r.get("topics") or {}
        means = _trial_means(topics) if isinstance(topics, dict) else None
        if means is None:
            continue
        cm, fm, overall = means
        out[key] = {"cohesion_mean": cm, "fit_mean": fm, "avg": overall}
    return out


def load_or_create_evaluation_order() -> list[dict[str, Any]]:
    if EVAL_ORDER_PATH.exists():
        try:
            with open(EVAL_ORDER_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            app.logger.warning("corrupt %s; regenerating", EVAL_ORDER_PATH.name)
    pool = [{"source": t["source"], "trial": t["trial"]} for t in top_n_per_source()]
    rng = random.SystemRandom()
    rng.shuffle(pool)
    order = [{"position": i, "source": p["source"], "trial": p["trial"]} for i, p in enumerate(pool)]
    atomic_write_json(EVAL_ORDER_PATH, order)
    return order


def resolve_position(order: list[dict[str, Any]], position: int) -> tuple[str, int]:
    if position < 0 or position >= len(order):
        abort(404, description=f"position {position} out of range (0..{len(order) - 1})")
    entry = order[position]
    return str(entry["source"]), int(entry["trial"])


def _trial_is_complete(source: str, trial: int, draft_entry: Any) -> bool:
    """A trial is complete when every leaf of that trial has both cohesion
    and fit set in the draft entry."""
    if not isinstance(draft_entry, dict):
        return False
    topics = draft_entry.get("topics")
    if not isinstance(topics, dict):
        return False
    for tid in trial_leaf_ids(source, trial):
        t = topics.get(tid)
        if not isinstance(t, dict):
            return False
        if not isinstance(t.get("cohesion"), int) or not isinstance(t.get("fit"), int):
            return False
    return True


def is_draft_complete(order: list[dict[str, Any]], draft: dict[str, Any]) -> bool:
    for entry in order:
        source = str(entry["source"])
        trial = int(entry["trial"])
        key = f"{source}:{trial}"
        if not _trial_is_complete(source, trial, draft.get(key)):
            return False
    return True


def _unrated_positions(order: list[dict[str, Any]], draft: dict[str, Any]) -> list[int]:
    out: list[int] = []
    for entry in order:
        source = str(entry["source"])
        trial = int(entry["trial"])
        key = f"{source}:{trial}"
        if not _trial_is_complete(source, trial, draft.get(key)):
            out.append(int(entry["position"]))
    return out


@app.route("/evaluation")
def evaluation_page():
    return render_template("evaluation.html")


@app.route("/api/evaluation/order")
def api_evaluation_order():
    return jsonify(load_or_create_evaluation_order())


@app.route("/api/evaluation/item/<int:position>")
def api_evaluation_item(position: int):
    order = load_or_create_evaluation_order()
    source, trial = resolve_position(order, position)
    path = trial_path(source, trial)
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    # Per-topic dendrogram colors are visual identity only — they do not
    # reveal the source folder or the DBCV/CCC scores, so they're safe to
    # serve on the blind page.
    info = get_trial_info(source, trial)
    taxonomy = normalize_node(raw)
    return jsonify({
        "taxonomy": taxonomy,
        "topic_colors": {str(k): v for k, v in info.get("colors", {}).items()},
        "leaf_ids": walk_leaf_topic_ids(taxonomy),
    })


@app.route("/api/evaluation/draft", methods=["GET"])
def api_evaluation_draft_get():
    return jsonify({
        "draft": load_evaluation_draft(),
        "submitted": load_evaluation_submitted() is not None,
    })


def _validate_cohesion(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in COHESION_BUCKETS:
        allowed = ", ".join(str(v) for v in COHESION_BUCKETS)
        abort(400, description=f"cohesion must be one of the bucket midpoints: {allowed}")
    return value


def _validate_fit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        abort(400, description=f"fit must be an integer in [{FIT_SCORE_MIN}, {FIT_SCORE_MAX}]")
    if value < FIT_SCORE_MIN or value > FIT_SCORE_MAX:
        abort(400, description=f"fit must be in [{FIT_SCORE_MIN}, {FIT_SCORE_MAX}]")
    return value


@app.route("/api/evaluation/draft/<int:position>", methods=["PUT"])
def api_evaluation_draft_put(position: int):
    if load_evaluation_submitted() is not None:
        abort(409, description="evaluation already submitted; draft is locked")
    order = load_or_create_evaluation_order()
    source, trial = resolve_position(order, position)
    body = request.get_json(force=True, silent=False) or {}
    if not isinstance(body, dict):
        abort(400, description="body must be an object")
    topic_id = body.get("topic_id")
    if not isinstance(topic_id, str) or not topic_id:
        abort(400, description="body must contain a string topic_id")
    if topic_id not in trial_leaf_ids(source, trial):
        abort(404, description=f"topic_id {topic_id!r} is not a leaf of this trial")
    if "cohesion" not in body and "fit" not in body:
        abort(400, description="body must contain cohesion and/or fit")

    draft = load_evaluation_draft()
    key = f"{source}:{trial}"
    entry = draft.get(key) if isinstance(draft.get(key), dict) else {}
    topics = entry.get("topics") if isinstance(entry.get("topics"), dict) else {}
    topic_entry = topics.get(topic_id) if isinstance(topics.get(topic_id), dict) else {}
    if "cohesion" in body:
        topic_entry["cohesion"] = _validate_cohesion(body.get("cohesion"))
    if "fit" in body:
        topic_entry["fit"] = _validate_fit(body.get("fit"))
    topics[topic_id] = topic_entry
    entry["topics"] = topics
    entry["updated_at"] = _utc_iso()
    draft[key] = entry
    save_evaluation_draft(draft)
    return jsonify({
        "ok": True,
        "trial_complete": _trial_is_complete(source, trial, entry),
        "all_complete": is_draft_complete(order, draft),
    })


@app.route("/api/evaluation/submit", methods=["POST"])
def api_evaluation_submit():
    # Allows overwriting a prior submission so the user can revise their
    # ratings via the "Modify ratings" flow on /evaluation.
    order = load_or_create_evaluation_order()
    draft = load_evaluation_draft()
    if not is_draft_complete(order, draft):
        return jsonify({
            "ok": False,
            "error": "draft incomplete",
            "unrated_positions": _unrated_positions(order, draft),
        }), 400
    ratings: dict[str, Any] = {}
    for entry_meta in order:
        source = str(entry_meta["source"])
        trial = int(entry_meta["trial"])
        key = f"{source}:{trial}"
        entry = draft.get(key) or {}
        topics_in = entry.get("topics") or {}
        # Strip incidental fields so the submitted payload only carries the
        # two per-topic scores that define this evaluation.
        topics_out = {
            tid: {"cohesion": t["cohesion"], "fit": t["fit"]}
            for tid, t in topics_in.items()
            if isinstance(t, dict)
            and isinstance(t.get("cohesion"), int)
            and isinstance(t.get("fit"), int)
        }
        means = _trial_means(topics_out)
        if means is None:
            # Should not happen — is_draft_complete already verified — but
            # guard against a half-empty trial entry slipping through.
            return jsonify({
                "ok": False,
                "error": "trial has no rated topics",
                "trial": key,
            }), 400
        cm, fm, overall = means
        ratings[key] = {
            "topics": topics_out,
            "cohesion_mean": round(cm, 4),
            "fit_mean": round(fm, 4),
            "overall": round(overall, 4),
        }
    # Pause the stopwatch and capture the duration alongside the ratings.
    timer_state = timer_action(EVAL_TIMER_PATH, "stop")
    payload = {
        "submitted_at": _utc_iso(),
        "duration_seconds": round(current_total_seconds(timer_state), 3),
        "ratings": ratings,
    }
    save_evaluation_submitted(payload)
    return jsonify({"ok": True, "duration_seconds": payload["duration_seconds"]})


@app.route("/api/evaluation/reset", methods=["POST"])
def api_evaluation_reset():
    """Wipe evaluation state (order, draft, submitted, timer) for a fresh blind
    pass. Leaves the moderated taxonomy untouched."""
    for p in (EVAL_ORDER_PATH, EVAL_DRAFT_PATH, EVAL_SUBMITTED_PATH, EVAL_TIMER_PATH):
        if p.exists():
            p.unlink()
    return jsonify({"ok": True})


@app.route("/api/evaluation/unlock", methods=["POST"])
def api_evaluation_unlock():
    """Re-open editing for a submitted evaluation. Pre-populates the draft from
    the submitted ratings and removes the submitted file so the existing draft
    PUT endpoint accepts writes again."""
    submitted = load_evaluation_submitted()
    if submitted is None:
        abort(409, description="no submitted evaluation to unlock")
    ratings = submitted.get("ratings") or {}
    draft = load_evaluation_draft()
    for key, r in ratings.items():
        if not isinstance(r, dict):
            continue
        topics_in = r.get("topics") or {}
        if not isinstance(topics_in, dict):
            continue
        entry = draft.get(key) if isinstance(draft.get(key), dict) else {}
        topics_out = entry.get("topics") if isinstance(entry.get("topics"), dict) else {}
        for tid, t in topics_in.items():
            if not isinstance(t, dict):
                continue
            te = topics_out.get(tid) if isinstance(topics_out.get(tid), dict) else {}
            if isinstance(t.get("cohesion"), int):
                te["cohesion"] = t["cohesion"]
            if isinstance(t.get("fit"), int):
                te["fit"] = t["fit"]
            topics_out[str(tid)] = te
        entry["topics"] = topics_out
        entry["updated_at"] = _utc_iso()
        draft[key] = entry
    save_evaluation_draft(draft)
    EVAL_SUBMITTED_PATH.unlink()
    return jsonify({"ok": True})


@app.route("/api/evaluation/results")
def api_evaluation_results():
    submitted = load_evaluation_submitted()
    if not submitted:
        abort(409, description="evaluation not submitted yet")
    order = load_or_create_evaluation_order()
    ratings = submitted.get("ratings") or {}
    out: list[dict[str, Any]] = []
    for entry in order:
        source = entry["source"]
        trial = int(entry["trial"])
        key = f"{source}:{trial}"
        meta = get_trial_meta(source, trial)
        dbcv = meta.get("dbcv")
        ccc = meta.get("ccc")
        avg_meta = (dbcv + ccc) / 2.0 if isinstance(dbcv, (int, float)) and isinstance(ccc, (int, float)) else None
        r = ratings.get(key) or {}
        cm = r.get("cohesion_mean")
        fm = r.get("fit_mean")
        ov = r.get("overall")
        out.append({
            "position": int(entry["position"]),
            "source": source,
            "source_label": SOURCES[source].label if source in SOURCES else source,
            "trial": trial,
            "dbcv": float(dbcv) if isinstance(dbcv, (int, float)) else None,
            "ccc": float(ccc) if isinstance(ccc, (int, float)) else None,
            "avg": avg_meta,
            "cohesion_mean": float(cm) if isinstance(cm, (int, float)) else None,
            "fit_mean": float(fm) if isinstance(fm, (int, float)) else None,
            "overall": float(ov) if isinstance(ov, (int, float)) else None,
            "topic_count": len(r.get("topics") or {}) if isinstance(r.get("topics"), dict) else 0,
        })
    return jsonify({
        "submitted_at": submitted.get("submitted_at"),
        "duration_seconds": submitted.get("duration_seconds"),
        "results": out,
    })


# --- Timer endpoints ----------------------------------------------------------

@app.route("/api/evaluation/timer", methods=["GET"])
def api_evaluation_timer_get():
    return _timer_response(load_timer(EVAL_TIMER_PATH))


@app.route("/api/evaluation/timer/start", methods=["POST"])
def api_evaluation_timer_start():
    return _timer_response(timer_action(EVAL_TIMER_PATH, "start"))


@app.route("/api/evaluation/timer/stop", methods=["POST"])
def api_evaluation_timer_stop():
    return _timer_response(timer_action(EVAL_TIMER_PATH, "stop"))


@app.route("/api/moderation/timer", methods=["GET"])
def api_moderation_timer_get():
    return _timer_response(load_timer(MOD_TIMER_PATH))


@app.route("/api/moderation/timer/start", methods=["POST"])
def api_moderation_timer_start():
    return _timer_response(timer_action(MOD_TIMER_PATH, "start", allow_finish=True))


@app.route("/api/moderation/timer/stop", methods=["POST"])
def api_moderation_timer_stop():
    return _timer_response(timer_action(MOD_TIMER_PATH, "stop", allow_finish=True))


@app.route("/api/moderation/timer/finish", methods=["POST"])
def api_moderation_timer_finish():
    return _timer_response(timer_action(MOD_TIMER_PATH, "finish", allow_finish=True))


@app.route("/api/moderation/timer/reopen", methods=["POST"])
def api_moderation_timer_reopen():
    return _timer_response(timer_action(MOD_TIMER_PATH, "reopen", allow_finish=True))


if __name__ == "__main__":
    print(f"TAXONOMY_DATA_ROOT = {DATA_ROOT}")
    print(f"workspace          = {WORKSPACE}")
    app.run(host="127.0.0.1", port=5000, debug=True)
