r"""Canonical data point catalogue.

Source of truth: the folder of per-data-point JSON files at
    ${TAXONOMY_DATA_ROOT}/data point descriptions/filtered_descriptions_Apertus-8B-Instruct-2509s/

Each file describes one data point with at least these fields:
    {
      "data_point": "<text>",
      "original_source": "<source.pdf>",
      "data_group": "<group label>",
      ...
    }

Trial-JSON strings have the form `"<text> (<source.pdf>)"` and map back to a
JSON file via the pair (normalized text, original_source). Normalization:
replace `&` with `and`, collapse whitespace, lowercase.

Two data points with the same text but different sources are distinct;
two with the same text and same source are duplicates.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

DP_RE = re.compile(r"^(.*) \(([^()]+\.pdf)\)$", re.DOTALL)


def normalize(s: object) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).replace("&", "and")).strip().lower()


def canonical_key(dp_string: str) -> str:
    """Stable canonical key for a trial-JSON data point string. Falls back to
    the raw lowercased string if the `(...)` source suffix is missing."""
    m = DP_RE.match(dp_string)
    if not m:
        return "raw::" + normalize(dp_string)
    return normalize(m.group(1)) + "||" + m.group(2)


@dataclass(frozen=True)
class CatalogueEntry:
    id: int
    text: str        # original data_point text
    file: str        # original_source PDF filename
    group: str       # data_group
    norm: str        # normalized text
    key: str         # canonical key = norm + "||" + file


class Catalogue:
    def __init__(self, entries: list[CatalogueEntry]):
        self.entries = entries
        self._by_key: dict[str, CatalogueEntry] = {e.key: e for e in entries}

    def __len__(self) -> int:
        return len(self.entries)

    def lookup(self, dp_string: str) -> Optional[CatalogueEntry]:
        return self._by_key.get(canonical_key(dp_string))

    def has(self, dp_string: str) -> bool:
        return canonical_key(dp_string) in self._by_key

    def coverage(self, dp_strings: Iterable[str]) -> dict[str, int]:
        keys = {canonical_key(dp) for dp in dp_strings}
        matched = sum(1 for k in keys if k in self._by_key)
        return {
            "trial_unique_keys": len(keys),
            "matched_to_catalogue": matched,
            "unmatched": len(keys) - matched,
            "catalogue_size": len(self),
        }


def load_catalogue(folder: Path) -> Catalogue:
    """Read every `*_response.json` in the folder and build the canonical
    catalogue. Files are sorted by name so the catalogue order is
    deterministic. Same-canonical-key entries across multiple files are
    collapsed to their first occurrence (~101 such duplicates exist)."""
    if not folder.is_dir():
        raise FileNotFoundError(f"catalogue folder not found: {folder}")
    entries: list[CatalogueEntry] = []
    seen: set[str] = set()
    for f in sorted(folder.glob("*_response.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        text = obj.get("data_point")
        file = obj.get("original_source")
        if not text or not file:
            continue
        text = str(text)
        file = str(file)
        norm = normalize(text)
        key = norm + "||" + file
        if key in seen:
            continue
        seen.add(key)
        entries.append(CatalogueEntry(
            id=len(entries),
            text=text,
            file=file,
            group=str(obj.get("data_group") or ""),
            norm=norm,
            key=key,
        ))
    return Catalogue(entries)
