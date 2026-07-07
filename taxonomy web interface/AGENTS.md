# AGENTS.md — hard constraints for this project

Any agent (including Claude Code, future LLM assistants, or human contributors using one) working in `taxonomy web interface/` MUST follow these rules. They are non-negotiable.

## 1. The parent data tree is strictly read-only

- **Never** create, edit, rename, move, or delete any file in the parent directory tree (`generated topic hierarchies/`, `data point descriptions/`, `moderated digital passport taxonomy/`, or any other sibling folder), or anywhere outside `taxonomy web interface/`.
- Those folders may have read-only permissions enforced by the OS. The constraint exists independently of those permissions — even if a write would succeed, you must not perform it.
- The Flask app reads source data from a configurable absolute path via the env var `TAXONOMY_DATA_ROOT` (default: the project root, i.e. the parent of this folder). All file opens against that root must be in read mode (`"r"`, `"rb"`). This includes:
  - `${TAXONOMY_DATA_ROOT}/generated topic hierarchies/<source>/*.json` (trial trees, from `Apertus-8B-Instruct`, `data_points`, `GPT5-nano`, and `Phi-4-mini`) and their companion `*.html` files.
  - `${TAXONOMY_DATA_ROOT}/data point descriptions/filtered_descriptions_Apertus-8B-Instruct-2509s/*_response.json` (canonical data point catalogue — see [README.md](README.md) for the canonical-key definition).
  - `${TAXONOMY_DATA_ROOT}/moderated digital passport taxonomy/251220 digital passport taxonomy viewer.html` (published reference taxonomy).
- If you need to derive a file from the data tree (e.g., the export template), you must **read** it from there and **write** the derivative inside `taxonomy web interface/`. The original is never touched.

## 2. Use the local virtual environment

- All Python work runs inside `taxonomy web interface/.venv/`.
- Never install into the system or user Python.
- All `pip install`, `python`, and `pytest` invocations go through the venv binary:
  - Windows: `.\.venv\Scripts\python.exe ...` and `.\.venv\Scripts\pip.exe ...`
  - POSIX: `./.venv/bin/python ...` and `./.venv/bin/pip ...`
- If `.venv/` does not exist, create it before doing anything else:

  ```powershell
  python -m venv .venv
  .\.venv\Scripts\python.exe -m pip install --upgrade pip
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  ```

## 3. All writable state lives in `taxonomy web interface/workspace/`

- The only writable directory is `taxonomy web interface/workspace/`.
- Do not write logs, caches, temp files, or anything else outside `taxonomy web interface/` (or, ideally, outside `workspace/`).
- `workspace/` is created at runtime by `app.py` if it does not exist.

## 4. Only the user starts the Flask server

- **Never** run `python app.py`, `flask run`, `gunicorn`, or anything else that boots the Flask development server.
- The user runs the server themselves from a terminal. Agents must not start, stop, or restart it, and must not background-run it.
- It is fine to write/edit `app.py`, `templates/index.html`, and other source files — but when changes need to be verified live, ask the user to start the server (or to share output/screenshots from their running session).
- Other read-only Python invocations (e.g., `python -c "import flask; print(flask.__version__)"` for diagnostics) are acceptable, as long as they don't bind a port or run a server loop.

## 5. The user commits git changes manually

- **Never** run `git add`, `git commit`, `git push`, `git tag`, or any other git command that mutates repo state. Read-only git commands (`git status`, `git log`, `git diff`) are fine for inspection.
- Do not stage files, do not propose commit messages unsolicited, do not create branches or PRs.
- Even when a task feels "done" or a milestone is reached, leave the working tree as-is. The user decides when and how to commit.
- If the user explicitly asks for help drafting a commit message or staging specific files, that override applies only to that one request.

## 6. No modifications outside `taxonomy web interface/`, period

This includes git config, environment files, system Python, user Python, the user's profile, IDE settings, or any sibling folder. If a task seems to require touching anything outside `taxonomy web interface/`, stop and ask the user first.
