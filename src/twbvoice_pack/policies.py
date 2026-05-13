"""Apply delivery policies to enriched records.

Operates on the enriched record list (output of `enrich.enrich_flow`) and
returns a filtered/tagged list plus a stats dict.

Policies applied:
- Drop rows flagged offensive at any reviewer stage (if drop_offensive=True).
- Drop pending rows (if include_pending=False).
- Tag a canonical transcription per audio file for freeform flows.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .config import FlowSpec, Policies
from .enrich import OFFENSIVE_FLAG


def _reviewer_lists(answer: dict) -> Iterable[dict]:
    yield from answer.get("reviewer_verdicts", []) or []
    yield from (answer.get("transcription_check") or {}).get("reviewer_verdicts", []) or []


def _is_offensive(answer: dict) -> bool:
    return any(
        OFFENSIVE_FLAG in (v.get("rejection_reasons") or [])
        for v in _reviewer_lists(answer)
    )


def _filename_of(r: dict, flow: FlowSpec) -> str:
    return r["content"][flow.record_filename_path]["filename"]


def _approval_status(r: dict, flow: FlowSpec) -> str:
    # Read flows: top-level approval_status reflects the recording-check.
    # Freeform flows: top-level approval_status reflects the transcription-check.
    return r.get("approval_status") or "pending"


def _mark_canonical_freeform(records: list[dict], strategy: str) -> None:
    """Mark exactly one row per filename as canonical for freeform flows.

    Strategy 'verified_then_latest':
        approved rows first; among those (or among all if none approved),
        pick the latest `created_at`.
    """
    by_file: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_file[r["content"]["input"]["filename"]].append(r)

    for filename, rows in by_file.items():
        approved = [r for r in rows if r.get("approval_status") == "approved"]
        pool = approved or rows
        if strategy == "verified_then_latest":
            canonical = max(pool, key=lambda r: r.get("created_at") or "")
        else:
            canonical = pool[0]
        for r in rows:
            r["content"]["answer"]["is_canonical_transcription"] = (r is canonical)


def apply(
    records: list[dict],
    flow: FlowSpec,
    policies: Policies,
) -> tuple[list[dict], dict]:
    dropped_offensive: list[str] = []
    dropped_pending: list[str] = []
    dropped_rejected: list[str] = []
    kept: list[dict] = []

    for r in records:
        ans = r["content"]["answer"]
        if policies.drop_offensive and _is_offensive(ans):
            dropped_offensive.append(_filename_of(r, flow))
            continue
        status = _approval_status(r, flow)
        if not policies.include_pending and status == "pending":
            dropped_pending.append(_filename_of(r, flow))
            continue
        if not policies.include_rejected and status == "rejected":
            dropped_rejected.append(_filename_of(r, flow))
            continue
        kept.append(r)

    if flow.has_transcription_field:
        _mark_canonical_freeform(kept, policies.canonical_strategy)

    stats = {
        "dropped_offensive": dropped_offensive,
        "dropped_pending": dropped_pending,
        "dropped_rejected": dropped_rejected,
    }
    return kept, stats
