"""
Copyright filter for LLM response JSONs.

Reads `<N>_response.json` files from `../data_point_descriptions/<model_folder>/`,
walks every `references[*]` entry, and replaces the verbatim `text` field with
a redaction notice when the source PDF is not on the "safe to extract" list
defined in `../data_point_descriptions/copyright details.txt`.

Filtered mirrors are written to `./<model_folder>/<filename>` (i.e. inside the
folder that contains this script). A per-folder + total summary table is
printed to stdout when the run finishes.

Run from anywhere:
    python filter_copyright.py
"""

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = SCRIPT_DIR.parent / "data_point_descriptions"
COPYRIGHT_TXT = SOURCE_ROOT / "copyright details.txt"

MODEL_FOLDERS = [
    "251029_generated_descriptions_Phi-4-mini-instruct",
    "251030_generated_descriptions_Apertus-8B-Instruct-2509s",
    "251030_generated_descriptions_GPT5-nano",
    "260603_generated_descriptions_Phi-4-mini-aligned",
]

REDACTION = "exact text reference is restricted by copyright of the original publication"


def load_copyright_config(path):
    """Execute `copyright details.txt` as Python and return its two literals."""
    namespace = {}
    exec(path.read_text(encoding="utf-8"), namespace)
    return namespace["apa_mapping"], namespace["dataset_config"]


def build_safe_filenames(apa_mapping, dataset_config):
    safe_apa = {row["name"] for row in dataset_config if row.get("can_extract")}
    return {fname for fname, apa in apa_mapping.items() if apa in safe_apa}


def filter_file(src_path, dst_path, apa_mapping, safe_filenames):
    data = json.loads(src_path.read_text(encoding="utf-8"))
    stats = {"total": 0, "kept": 0, "redacted_restricted": 0, "redacted_unknown": 0}

    for ref in data.get("references", []):
        stats["total"] += 1
        fname = (ref.get("metadata") or {}).get("file_name")
        if fname in safe_filenames:
            stats["kept"] += 1
        elif fname in apa_mapping:
            ref["text"] = REDACTION
            stats["redacted_restricted"] += 1
        else:
            ref["text"] = REDACTION
            stats["redacted_unknown"] += 1

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(
        json.dumps(data, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )
    return stats


def process_folder(folder_name, apa_mapping, safe_filenames):
    src_dir = SOURCE_ROOT / folder_name
    dst_dir = SCRIPT_DIR / folder_name
    agg = {"files": 0, "total": 0, "kept": 0, "redacted_restricted": 0, "redacted_unknown": 0}

    if not src_dir.is_dir():
        print(f"warn: source folder missing: {src_dir}", file=sys.stderr)
        return agg

    for src in sorted(src_dir.glob("*_response.json")):
        per_file = filter_file(src, dst_dir / src.name, apa_mapping, safe_filenames)
        agg["files"] += 1
        for key in ("total", "kept", "redacted_restricted", "redacted_unknown"):
            agg[key] += per_file[key]
    return agg


def print_summary(per_folder, grand):
    headers = (
        "Model folder",
        "Files",
        "Total refs",
        "Kept",
        "Redacted (restricted)",
        "Redacted (unknown PDF)",
    )
    rows = [
        (
            folder,
            agg["files"],
            agg["total"],
            agg["kept"],
            agg["redacted_restricted"],
            agg["redacted_unknown"],
        )
        for folder, agg in per_folder
    ]
    rows.append((
        "TOTAL",
        grand["files"],
        grand["total"],
        grand["kept"],
        grand["redacted_restricted"],
        grand["redacted_unknown"],
    ))

    widths = [max(len(str(r[i])) for r in (headers, *rows)) for i in range(len(headers))]
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    sep = "  ".join("-" * w for w in widths)

    print()
    print(fmt.format(*headers))
    print(sep)
    for row in rows[:-1]:
        print(fmt.format(*row))
    print(sep)
    print(fmt.format(*rows[-1]))


def main():
    if not COPYRIGHT_TXT.exists():
        print(f"error: copyright config not found at {COPYRIGHT_TXT}", file=sys.stderr)
        return 1

    apa_mapping, dataset_config = load_copyright_config(COPYRIGHT_TXT)
    safe_filenames = build_safe_filenames(apa_mapping, dataset_config)

    per_folder = []
    grand = {"files": 0, "total": 0, "kept": 0, "redacted_restricted": 0, "redacted_unknown": 0}

    for folder in MODEL_FOLDERS:
        agg = process_folder(folder, apa_mapping, safe_filenames)
        per_folder.append((folder, agg))
        for key in grand:
            grand[key] += agg[key]

    print_summary(per_folder, grand)
    return 0


if __name__ == "__main__":
    sys.exit(main())
