"""Compute dataset-card statistics from enriched, policy-applied records.

Everything here is pure aggregation. No filtering decisions live in this module.
"""

from __future__ import annotations

import datetime as dt
import statistics as st
from collections import Counter
from typing import Iterable

from .config import FlowSpec


def _age_buckets(year_of_birth: int | None, recording_year: int | None) -> str | None:
    if not year_of_birth or not recording_year:
        return None
    age = recording_year - int(year_of_birth)
    if age < 18:
        return "under 18"
    if age < 30:
        return "18-29"
    if age < 45:
        return "30-44"
    if age < 60:
        return "45-59"
    return "60+"


def _recording_year(r: dict) -> int | None:
    ts = r.get("created_at")
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace(" ", "T")).year
    except ValueError:
        return None


def _duration(r: dict) -> float | None:
    d = r["content"]["input"].get("duration")
    if d is None:
        d = r["content"]["answer"].get("duration")
    try:
        return float(d) if d is not None else None
    except (TypeError, ValueError):
        return None


def _normalize_country(v):
    if not isinstance(v, str):
        return v
    return v.strip().title() or v


def _user(r: dict) -> dict:
    u = dict((r["content"]["metadata"] or {}).get("user") or {})
    # Source data sometimes mixes "Nigeria" and "NIGERIA"; normalise so they
    # collapse in distributions.
    if u.get("country_of_origin"):
        u["country_of_origin"] = _normalize_country(u["country_of_origin"])
    return u


def _iter_with_demographics(records: list[dict]) -> Iterable[tuple[dict, float | None]]:
    for r in records:
        yield r, _duration(r)


def analyze_flow(records: list[dict], flow: FlowSpec) -> dict:
    """Return a stats dict ready for card template rendering.

    For flows with a transcription field (freeform), the same audio file may
    appear in multiple rows (one per transcriber). Per-audio statistics
    (hours, demographics, durations) are computed on the *canonical* row only
    so durations and speakers aren't double-counted. Approval-status counts
    remain row-level since they describe transcription verdicts.
    """
    if flow.has_transcription_field:
        per_audio = [r for r in records if r["content"]["answer"].get("is_canonical_transcription")]
    else:
        per_audio = records

    durations = [d for _, d in _iter_with_demographics(per_audio) if d is not None]
    total_seconds = sum(durations)

    # By approval status — row-level (transcription verdicts)
    approval = Counter(r.get("approval_status") or "unknown" for r in records)
    hours_by_status: dict[str, float] = {}
    for r in per_audio:
        s = r.get("approval_status") or "unknown"
        d = _duration(r)
        if d:
            hours_by_status[s] = hours_by_status.get(s, 0.0) + d / 3600.0

    # Approved-only subset, used for demographic distributions (HF convention).
    approved_audio = [r for r in per_audio if r.get("approval_status") == "approved"]

    by_speaker_approved: dict[str, dict] = {}
    for r in approved_audio:
        uid = r.get("hashed_user_id")
        if uid and uid not in by_speaker_approved:
            by_speaker_approved[uid] = _user(r)

    def _hours_by(field: str, pool: list[dict], age: bool = False) -> dict[str, float]:
        h: dict[str, float] = {}
        for r in pool:
            d = _duration(r) or 0.0
            u = _user(r)
            if age:
                key = _age_buckets(u.get("year_of_birth"), _recording_year(r)) or "unknown"
            else:
                key = (u.get(field) or "unknown") or "unknown"
            h[key] = h.get(key, 0.0) + d / 3600.0
        return h

    def _speakers_by(field: str, pool: list[dict], age: bool = False) -> dict[str, int]:
        per_speaker_categories: dict[str, set[str]] = {}
        for r in pool:
            uid = r.get("hashed_user_id")
            if not uid:
                continue
            u = _user(r)
            if age:
                key = _age_buckets(u.get("year_of_birth"), _recording_year(r)) or "unknown"
            else:
                key = (u.get(field) or "unknown") or "unknown"
            per_speaker_categories.setdefault(uid, set()).add(key)
        out: Counter[str] = Counter()
        for uid, keys in per_speaker_categories.items():
            for k in keys:
                out[k] += 1
        return dict(out)

    hours_by_gender_approved = _hours_by("gender", approved_audio)
    hours_by_age_approved = _hours_by("", approved_audio, age=True)
    hours_by_country_approved = _hours_by("country_of_origin", approved_audio)
    hours_by_variant_approved = _hours_by("language_variant", approved_audio)
    hours_by_education_approved = _hours_by("education_level", approved_audio)

    speakers_by_gender_approved = _speakers_by("gender", approved_audio)
    speakers_by_age_approved = _speakers_by("", approved_audio, age=True)
    speakers_by_country_approved = _speakers_by("country_of_origin", approved_audio)
    speakers_by_variant_approved = _speakers_by("language_variant", approved_audio)

    # Rejection reasons (rec-check + tx-check)
    rec_reasons: Counter[str] = Counter()
    trn_reasons: Counter[str] = Counter()
    for r in records:
        for v in r["content"]["answer"].get("reviewer_verdicts") or []:
            for x in v.get("rejection_reasons") or []:
                rec_reasons[x] += 1
        for v in (r["content"]["answer"].get("transcription_check") or {}).get(
            "reviewer_verdicts"
        ) or []:
            for x in v.get("rejection_reasons") or []:
                trn_reasons[x] += 1

    # Multi-rater coverage at recording-check
    rater_counts: Counter[int] = Counter()
    for r in records:
        rater_counts[len(r["content"]["answer"].get("reviewer_verdicts") or [])] += 1

    # Duration sanity
    dur_stats = {}
    if durations:
        s = sorted(durations)
        dur_stats = {
            "min": s[0],
            "p05": s[max(0, len(s) // 20)],
            "p50": st.median(s),
            "p95": s[min(len(s) - 1, len(s) * 19 // 20)],
            "max": s[-1],
            "mean": st.mean(s),
            "total_hours": total_seconds / 3600.0,
        }

    # Canonical transcription count (freeform only)
    canonical_count = sum(
        1
        for r in records
        if r["content"]["answer"].get("is_canonical_transcription")
    )

    return {
        "rows": len(records),
        "distinct_files": len(
            {r["content"][flow.record_filename_path]["filename"] for r in records}
        ),
        "distinct_speakers_approved": len(by_speaker_approved),
        "approval": dict(approval),
        "hours_by_status": hours_by_status,
        "durations": dur_stats,
        # Approved-only demographic distributions (HF convention)
        "hours_by_gender_approved": hours_by_gender_approved,
        "hours_by_age_approved": hours_by_age_approved,
        "hours_by_country_approved": hours_by_country_approved,
        "hours_by_variant_approved": hours_by_variant_approved,
        "hours_by_education_approved": hours_by_education_approved,
        "speakers_by_gender_approved": speakers_by_gender_approved,
        "speakers_by_age_approved": speakers_by_age_approved,
        "speakers_by_country_approved": speakers_by_country_approved,
        "speakers_by_variant_approved": speakers_by_variant_approved,
        # Verdict-level tallies are intentionally over all records (not just approved)
        "recording_check_reasons": dict(rec_reasons),
        "transcription_check_reasons": dict(trn_reasons),
        "rater_count_histogram": dict(rater_counts),
        "canonical_transcription_count": canonical_count,
    }
