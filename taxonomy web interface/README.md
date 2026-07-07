# Taxonomy Curator

A Flask single-page app for the expert step ("step 4") of the taxonomy workflow.
The left pane shows the automatically generated binary tree taxonomies from
`../generated topic hierarchies/`; the right pane is a moderated taxonomy that
the expert builds via drag-and-drop, renaming, adding/removing layers, and
reordering. A blind qualitative evaluation page (`/evaluation`) precedes the
moderation workbench: first-time users are routed there until every leaf of
every selected trial has been rated.

## Setup (one-time)

From inside `taxonomy web interface/`:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run the Flask server

```powershell
.\.venv\Scripts\python.exe app.py
```

Then open <http://127.0.0.1:5000> in a browser.

> **Only the user (project owner) starts the Flask server.** Agents (Claude
> Code or any other LLM assistant) MUST NOT run `python app.py`, `flask run`,
> or anything that boots the server. They may write/edit code and run
> read-only inspection commands, but starting the server is a human-only
> action. See [AGENTS.md](AGENTS.md) for the full rule set.

## Configuration

- `TAXONOMY_DATA_ROOT` — absolute path to the read-only data root.
  Default: the project root, i.e. the parent of this folder (resolved
  relative to `app.py`).
- The app reads:
  - `${TAXONOMY_DATA_ROOT}/generated topic hierarchies/<source>/*.json` —
    trial trees, from the four source folders `Apertus-8B-Instruct`,
    `data_points`, `GPT5-nano`, and `Phi-4-mini` (plus their companion
    `*.html` dendrogram/combined-plot files for colors and DBCV/CCC metadata).
  - `${TAXONOMY_DATA_ROOT}/data point descriptions/filtered_descriptions_Apertus-8B-Instruct-2509s/*_response.json` —
    the canonical data point catalogue (1909 entries).
  - `${TAXONOMY_DATA_ROOT}/moderated digital passport taxonomy/251220 digital passport taxonomy viewer.html` —
    the published reference taxonomy, offered as a drag source / seed in the
    trial picker.

## Canonical catalogue

The folder of per-data-point JSON files is the source of what counts as a
single data point. Each data point has a stable identity given by
`(normalized_text, source_file)`, where the text is lowercased,
whitespace-collapsed, and `&` is replaced with `and` (compound items appear
with `&` in some catalogue entries while the trial JSONs render them with
`and`).

This identity is used for:

- **Placed tracking** — when a data point is dropped into the moderated taxonomy
  on the right, every occurrence of the *same canonical key* in the left pane is
  greyed out, no matter which trial is selected.
- **Seed dedup** — when `POST /api/init-moderated` copies a trial in, data points
  with duplicate canonical keys are collapsed to their first occurrence (some
  Apertus trees contain 5–10 such duplicates).

Trial coverage of the catalogue ranges from 53% (data_points trial 188 = 1012
of 1909) to 84% (data_points trial 223 = 1609 of 1909). 30 catalogue entries
never appear in any trial output.

## Where things live

| Path | What |
| --- | --- |
| `app.py` | Flask server: trial discovery, schema normalization, blind evaluation, atomic saves, JSON/HTML export. |
| `catalogue.py` | Loads the JSON catalogue folder and computes canonical data-point keys. |
| `dendrogram_colors.py` | Parses trial HTML files for per-topic colors and DBCV/CCC metadata. |
| `templates/index.html` | Two-pane moderation UI (Tailwind + SortableJS + vanilla JS). |
| `templates/evaluation.html` | Blind per-leaf evaluation UI (cohesion + hierarchical fit). |
| `static/viewer_template.html` | Copy of the 251220 viewer with a `__CLUSTER_DATA__` marker, used to render standalone HTML exports. |
| `workspace/moderated_taxonomy.json` | Live state of the right pane. Auto-saved on every edit. |
| `workspace/placed_keys.json` | Canonical keys already placed on the right pane (drives greying on the left). |
| `workspace/evaluation_*.json` | Blind evaluation state: shuffled order, draft scores, submitted ratings, stopwatch. |
| `workspace/top_trials.json` | Cached top-trials-per-source selection; delete to recompute after adding/removing trials. |
| `requirements.txt` | `flask` only. |
| `.venv/` | Local Python environment for this app. |
| `AGENTS.md` | Hard constraints for any agent working in this folder. |

## Export

- **JSON** (`GET /api/export/json`) — moderated taxonomy in the viewer schema
  (`topic_id`/`topic_name`/`children`/`data_points`). Directly loadable by the
  existing 251220 viewer.
- **HTML** (`GET /api/export/html`) — a single self-contained HTML file modeled
  on the 251220 viewer, with the moderated taxonomy embedded.

## Endpoints (REST)

Moderation:

- `GET /api/trials` — reference taxonomy + top trials per source (by avg DBCV/CCC), with the user's submitted ratings.
- `GET /api/trial/<source>/<trial>` — load one trial, normalized to the viewer schema, plus the current `placed` set of canonical keys.
- `GET /api/catalogue` — `{size, path}` for the catalogue folder.
- `GET /api/moderated` — current moderated taxonomy + placed keys.
- `POST /api/moderated` — full-state replace; recomputes the placed set.
- `POST /api/init-moderated` — seed the moderated taxonomy from a chosen trial (dedupes by canonical key).
- `POST /api/reset` — wipe moderation state.
- `GET /api/export/json` — download moderated taxonomy in viewer schema.
- `GET /api/export/html` — download a self-contained HTML viewer with the moderated taxonomy embedded.

Blind evaluation:

- `GET /api/evaluation/order` — shuffled `(position, source, trial)` list (created on first call).
- `GET /api/evaluation/item/<position>` — one trial's taxonomy + topic colors + leaf ids, without revealing its source.
- `GET /api/evaluation/draft` / `PUT /api/evaluation/draft/<position>` — read / auto-save per-leaf cohesion & fit scores.
- `POST /api/evaluation/submit` — freeze the ratings and compute per-trial means.
- `POST /api/evaluation/unlock` — re-open a submitted evaluation for editing.
- `POST /api/evaluation/reset` — wipe evaluation state (order, draft, submitted, timer).
- `GET /api/evaluation/results` — submitted ratings alongside DBCV/CCC per trial.
- `GET|POST /api/evaluation/timer[...]`, `GET|POST /api/moderation/timer[...]` — manual stopwatch state / start / stop (moderation also: finish, reopen).

## Reset

`POST /api/reset` deletes `workspace/moderated_taxonomy.json`,
`workspace/placed_keys.json`, and `workspace/moderation_timer.json`, returning
the app to the initial "choose a trial to seed from" state. The UI has a Reset
button that calls this endpoint. Evaluation state is reset separately via
`POST /api/evaluation/reset`.
