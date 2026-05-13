"""Enrich record-step rows with reviewer verdicts from optional check-step JSONs.

Schema additions (under `content.answer`):
    rejection_reasons       list[str]   union across rejecting reviewers (step 2)
    rejection_comment       str | None  first non-empty comment (step 2)
    reviewer_verdicts       list[dict]  per-reviewer detail (step 2)
    transcription_check     dict        same three fields for step 4 (freeform only)
    is_canonical_transcription bool     freeform only; set by policies module

If a check-step JSON is missing, the corresponding fields are still present
with empty values, so downstream consumers can rely on the schema.
"""

from __future__ import annotations

import copy
import json
import zipfile
from collections import defaultdict
from pathlib import Path

from .config import FlowSpec

PII_FLAG = (
    "Contains personally identifiable information "
    "(e.g. name, address, telephone number)"
)
OFFENSIVE_FLAG = "Contains offensive or inappropriate content"


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #


def load_record_step(zip_path: Path) -> list[dict]:
    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(
            (n for n in zf.namelist() if n.endswith("data.json")),
            key=lambda n: n.count("/"),
        )
        if not names:
            raise ValueError(f"No data.json in {zip_path}")
        with zf.open(names[0]) as f:
            return json.load(f)


def load_check_step(path: Path | None) -> list[dict]:
    if path is None:
        return []
    with open(path) as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# Indexing & aggregation
# --------------------------------------------------------------------------- #


def _index_by_filename(validations: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = defaultdict(list)
    for v in validations:
        g[v["content"]["input"].get("filename")].append(v)
    return g


def _index_by_filename_and_transcription(validations: list[dict]):
    g: dict[tuple, list[dict]] = defaultdict(list)
    for v in validations:
        key = (
            v["content"]["input"].get("filename"),
            v["content"]["input"].get("transcription"),
        )
        g[key].append(v)
    return g


def _reviewer_verdicts(vlist: list[dict]) -> list[dict]:
    return [
        {
            "accepted": v["content"]["answer"].get("accepted"),
            "weighted_score": v["content"]["answer"].get("weighted_score"),
            "rejection_reasons": v["content"]["answer"].get("rejection_reasons") or [],
            "rejection_comment": v["content"]["answer"].get("rejection_comment"),
        }
        for v in vlist
    ]


def _aggregate(vlist: list[dict]):
    rv = _reviewer_verdicts(vlist)
    reasons, comment, seen = [], None, set()
    for v in rv:
        if v["accepted"] is False:
            for r in v["rejection_reasons"]:
                if r not in seen:
                    reasons.append(r)
                    seen.add(r)
            if comment is None and v["rejection_comment"]:
                comment = v["rejection_comment"]
    return reasons, comment, rv


def _has_flag(vlist: list[dict], flag: str) -> bool:
    return any(
        flag in (v["content"]["answer"].get("rejection_reasons") or []) for v in vlist
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def enrich_flow(flow: FlowSpec) -> tuple[list[dict], dict]:
    """Enrich a flow's records. Returns (enriched_records, stats)."""
    records = load_record_step(flow.record_zip)
    rec_check = load_check_step(flow.recording_check)
    trn_check = load_check_step(flow.transcription_check)

    rec_idx = _index_by_filename(rec_check)
    trn_idx = (
        _index_by_filename_and_transcription(trn_check) if trn_check else None
    )

    out: list[dict] = []
    pii_dropped: list[str] = []

    for r in records:
        filename = r["content"][flow.record_filename_path]["filename"]
        rec_v = rec_idx.get(filename, [])
        if _has_flag(rec_v, PII_FLAG):
            pii_dropped.append(filename)
            continue

        new = copy.deepcopy(r)
        reasons, comment, rv = _aggregate(rec_v)
        new["content"]["answer"]["rejection_reasons"] = reasons
        new["content"]["answer"]["rejection_comment"] = comment
        new["content"]["answer"]["reviewer_verdicts"] = rv

        if flow.has_transcription_field:
            if trn_idx is not None:
                tx = r["content"]["answer"].get("transcription")
                trn_v = trn_idx.get((filename, tx), [])
                t_reasons, t_comment, t_rv = _aggregate(trn_v)
                new["content"]["answer"]["transcription_check"] = {
                    "rejection_reasons": t_reasons,
                    "rejection_comment": t_comment,
                    "reviewer_verdicts": t_rv,
                }
            else:
                new["content"]["answer"]["transcription_check"] = {
                    "rejection_reasons": [],
                    "rejection_comment": None,
                    "reviewer_verdicts": [],
                }

        out.append(new)

    stats = {
        "input_rows": len(records),
        "output_rows": len(out),
        "pii_dropped": pii_dropped,
        "has_recording_check": bool(rec_check),
        "has_transcription_check": bool(trn_check),
    }
    return out, stats
