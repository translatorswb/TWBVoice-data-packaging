"""Build the shippable tarball — one per delivery, in a single pass.

Layout inside the tarball:
    <slug>/
        audio/<flow>/<approval_status>/<filename>.wav
        metadata.json
        dataset_card.md
        README.txt
        LICENSE                (only if a license file was provided)
"""

from __future__ import annotations

import copy
import io
import json
import tarfile
import zipfile
from pathlib import Path

from .config import Config, FlowSpec


README_TXT = """\
{title}

Files in this archive
---------------------
- audio/<flow>/<approval_status>/*.wav
                          WAV recordings, organised by pipeline flow and by
                          the final approval status of the row that references
                          them. The `audio_path` field on each record points
                          to its location relative to this folder.
- metadata.json           List of records across all flows. Each row carries a
                          `flow` field identifying its source flow plus the
                          enriched reviewer-verdict and rejection-reason
                          fields under content.answer.
- dataset_card.md         Statistics, demographics, caveats.
- LICENSE                 (only present when the operator provided one)

Join audio to metadata on `audio_path`.
"""


def _filename(r: dict, flow: FlowSpec) -> str:
    return r["content"][flow.record_filename_path]["filename"]


def _status(r: dict) -> str:
    return r.get("approval_status") or "unknown"


def stamp_paths(
    records_by_flow: dict[str, tuple[FlowSpec, list[dict]]],
) -> list[dict]:
    """Attach `flow` and `audio_path` to a deep copy of each record."""
    flat: list[dict] = []
    for flow_name, (flow, records) in records_by_flow.items():
        for r in records:
            new = copy.deepcopy(r)
            fn = _filename(new, flow)
            new["flow"] = flow_name
            new["audio_path"] = f"audio/{flow_name}/{_status(new)}/{fn}"
            flat.append(new)
    return flat


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def build(
    cfg: Config,
    records_by_flow: dict[str, tuple[FlowSpec, list[dict]]],
    card_md: str,
) -> tuple[Path, list[dict], int]:
    """Build the per-delivery tarball in one pass.

    Returns (tarball_path, stamped_records, sum_of_audio_bytes).
    """
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    slug = cfg.slug
    out_path = cfg.output_dir / f"{slug}.tar.gz"

    flat = stamp_paths(records_by_flow)
    metadata_blob = json.dumps(flat, ensure_ascii=False, indent=2).encode("utf-8")

    title = (
        f"{cfg.delivery.language.name} ({cfg.delivery.language.code}) — {cfg.delivery.date}"
    )
    if cfg.card.pretty_name:
        title = cfg.card.pretty_name
    readme = README_TXT.format(title=title).encode("utf-8")

    audio_bytes_total = 0

    with tarfile.open(out_path, "w:gz") as tar:
        # Audio first, grouped by flow and status
        for flow_name, (flow, records) in records_by_flow.items():
            # Choose the canonical row's status when a single audio is
            # referenced by multiple rows (freeform with duplicate transcriptions).
            file_status: dict[str, str] = {}
            for r in records:
                fn = _filename(r, flow)
                if flow.has_transcription_field:
                    if r["content"]["answer"].get("is_canonical_transcription"):
                        file_status[fn] = _status(r)
                    else:
                        file_status.setdefault(fn, _status(r))
                else:
                    file_status[fn] = _status(r)

            with zipfile.ZipFile(flow.record_zip) as zf:
                for name in zf.namelist():
                    if not name.endswith(".wav"):
                        continue
                    base = name.split("/")[-1]
                    if base not in file_status:
                        continue
                    status = file_status[base]
                    with zf.open(name) as src:
                        data = src.read()
                    audio_bytes_total += len(data)
                    _add_bytes(
                        tar,
                        f"{slug}/audio/{flow_name}/{status}/{base}",
                        data,
                    )

        # Text artifacts last
        _add_bytes(tar, f"{slug}/metadata.json", metadata_blob)
        _add_bytes(tar, f"{slug}/dataset_card.md", card_md.encode("utf-8"))
        _add_bytes(tar, f"{slug}/README.txt", readme)

        if cfg.card.license_file and Path(cfg.card.license_file).exists():
            _add_bytes(
                tar,
                f"{slug}/LICENSE",
                Path(cfg.card.license_file).read_bytes(),
            )

    return out_path, flat, audio_bytes_total
