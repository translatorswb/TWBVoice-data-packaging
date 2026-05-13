"""Pre-shipment health checks: file integrity, duration sanity, coverage."""

from __future__ import annotations

import statistics as st
import zipfile
from pathlib import Path

from .config import FlowSpec


def _zip_audio_files(zip_path: Path) -> set[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return {
            n.split("/")[-1]
            for n in zf.namelist()
            if "audio/" in n and n.endswith(".wav") and not n.endswith("/")
        }


def check_flow(records: list[dict], flow: FlowSpec) -> dict:
    """Run all checks for one flow; return a report dict (warnings, not errors)."""
    zip_files = _zip_audio_files(flow.record_zip)
    md_files = {r["content"][flow.record_filename_path]["filename"] for r in records}

    missing_audio = sorted(md_files - zip_files)
    orphan_audio = sorted(zip_files - md_files)

    # Duration field: in record-step rows it's either at input.duration (freeform)
    # or answer.duration (read). Try both.
    durations: list[float] = []
    for r in records:
        d = r["content"]["input"].get("duration")
        if d is None:
            d = r["content"]["answer"].get("duration")
        if d is not None:
            durations.append(float(d))

    dur_stats = {}
    if durations:
        s = sorted(durations)
        dur_stats = {
            "n": len(s),
            "min": s[0],
            "p05": s[max(0, len(s) // 20)],
            "p50": st.median(s),
            "p95": s[min(len(s) - 1, len(s) * 19 // 20)],
            "max": s[-1],
            "mean": st.mean(s),
            "total_seconds": sum(s),
            "total_hours": sum(s) / 3600.0,
            "outliers_under_2s": sum(1 for x in s if x < 2),
            "outliers_over_60s": sum(1 for x in s if x > 60),
        }

    # Coverage by recording-check verdicts (whether each row got at least one)
    if flow.recording_check is not None:
        with_verdict = sum(
            1 for r in records if r["content"]["answer"].get("reviewer_verdicts")
        )
        coverage = {"with_recording_verdict": with_verdict, "total": len(records)}
    else:
        coverage = {"with_recording_verdict": 0, "total": len(records)}

    return {
        "zip_audio_count": len(zip_files),
        "metadata_file_count": len(md_files),
        "missing_audio": missing_audio,
        "orphan_audio": orphan_audio,
        "durations": dur_stats,
        "coverage": coverage,
    }


def format_report(report: dict) -> list[str]:
    """Return lines of a human-readable summary."""
    lines: list[str] = []
    if report["zip_audio_count"] == 0:
        # Metadata-only build: the zip carries no audio. Treat as informational,
        # not a warning.
        lines.append(
            f"  ℹ metadata-only zip (no audio); metadata references {report['metadata_file_count']} files"
        )
    else:
        lines.append(
            f"  zip audio: {report['zip_audio_count']}, metadata files: {report['metadata_file_count']}"
        )
        if report["missing_audio"]:
            lines.append(f"  ⚠ {len(report['missing_audio'])} audio missing from zip")
        # Orphans (zip has audio that metadata no longer references) are
        # expected when the policy filter drops rows — informational only.
        if not report["missing_audio"]:
            lines.append("  ✓ filename integrity")
    d = report["durations"]
    if d:
        lines.append(
            f"  durations: min={d['min']:.1f}s  p50={d['p50']:.1f}s  "
            f"p95={d['p95']:.1f}s  max={d['max']:.1f}s  total={d['total_hours']:.2f}h"
        )
        if d["outliers_under_2s"] or d["outliers_over_60s"]:
            lines.append(
                f"  ⚠ duration outliers: <2s={d['outliers_under_2s']}, "
                f">60s={d['outliers_over_60s']}"
            )
    cov = report["coverage"]
    if cov["total"]:
        gap = cov["total"] - cov["with_recording_verdict"]
        if gap:
            lines.append(
                f"  ⚠ {gap} row(s) without recording-check verdict"
            )
        else:
            lines.append("  ✓ all rows have recording-check verdicts")
    return lines
