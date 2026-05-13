"""YAML configuration loading and validation for a delivery."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# --------------------------------------------------------------------------- #
# Schema dataclasses
# --------------------------------------------------------------------------- #


@dataclass
class LanguageSpec:
    code: str
    name: str


@dataclass
class FlowSpec:
    """One pipeline flow within a delivery (typically 'freeform' or 'read')."""

    name: str
    record_zip: Path
    recording_check: Path | None = None
    transcription_check: Path | None = None
    # Inferred at load time from the record-step JSON inside the zip:
    record_filename_path: str = "input"   # "input" or "answer"
    has_transcription_field: bool = False


@dataclass
class Policies:
    include_pending: bool = True
    include_rejected: bool = True
    drop_offensive: bool = True
    duplicate_transcriptions: str = "all_mark_canonical"   # "all_mark_canonical" | "approved_only"
    canonical_strategy: str = "verified_then_latest"       # see policies.py


@dataclass
class DeliverySpec:
    """Internal/admin info about a delivery (not surfaced to partners)."""

    date: str
    language: LanguageSpec
    partner: str | None = None


@dataclass
class CardSpec:
    """Everything that ends up in the dataset card.

    Anything project-specific (project name, tags, citation, contact, license,
    etc.) belongs here. The template renders only what's provided — empty
    sections are skipped.
    """

    pretty_name: str | None = None                       # title shown in the card
    description: str | None = None                       # body text right under the title
    task_categories: list[str] = field(default_factory=lambda: ["automatic-speech-recognition"])
    tags: list[str] = field(default_factory=list)        # extra tags; language code auto-added
    size_categories: str | None = None                   # e.g. "10K<n<100K"; auto-derived if omitted
    license_id: str | None = None                        # SPDX-style string
    license_file: Path | None = None                     # optional path to a LICENSE file to bundle
    citation: str | None = None
    contact: str | None = None
    acknowledgments: str | None = None


@dataclass
class Config:
    delivery: DeliverySpec
    flows: dict[str, FlowSpec]
    policies: Policies
    card: CardSpec
    config_path: Path
    output_dir: Path

    @property
    def slug(self) -> str:
        return f"{self.delivery.language.code}_{self.delivery.date}"


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #


def _resolve(base: Path, p: str | Path | None) -> Path | None:
    if p is None:
        return None
    p = Path(p)
    return p if p.is_absolute() else (base / p).resolve()


def _sniff_record_step(zip_path: Path) -> tuple[str, bool]:
    """Open the zip, find data.json, return (filename_path, has_transcription).

    filename_path is "input" or "answer" depending on which side of `content`
    carries the audio filename. Has_transcription is True when freeform-style
    rows have a transcription field under `content.answer`.
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.endswith("data.json")]
        if not names:
            raise ValueError(f"No data.json in {zip_path}")
        # Pick the shallowest data.json
        names.sort(key=lambda n: n.count("/"))
        with zf.open(names[0]) as f:
            records = json.load(f)
    if not records:
        raise ValueError(f"Empty record list in {zip_path}:{names[0]}")
    r = records[0]
    inp = r.get("content", {}).get("input", {})
    ans = r.get("content", {}).get("answer", {})
    if "filename" in inp:
        return "input", "transcription" in ans
    if "filename" in ans:
        return "answer", "transcription" in ans
    raise ValueError(f"Cannot locate filename in record-step rows of {zip_path}")


def load(path: str | Path, output_dir: str | Path | None = None) -> Config:
    """Load + validate a delivery YAML."""
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    base = path.parent
    with open(path) as f:
        raw = yaml.safe_load(f)

    # ----- delivery block (internal admin only) -----
    dblk = raw.get("delivery") or {}
    lang = dblk.get("language") or {}
    if isinstance(lang, str):
        lang_spec = LanguageSpec(code=lang, name=lang)
    else:
        lang_spec = LanguageSpec(code=lang["code"], name=lang.get("name", lang["code"]))
    delivery = DeliverySpec(
        date=str(dblk["date"]),
        language=lang_spec,
        partner=dblk.get("partner"),
    )

    # ----- card block (all partner-facing content) -----
    cblk = raw.get("card") or {}
    lic = cblk.get("license")
    license_id: str | None = None
    license_file: Path | None = None
    if isinstance(lic, dict):
        license_id = lic.get("id")
        license_file = _resolve(base, lic.get("file"))
    elif isinstance(lic, str) and lic:
        candidate = _resolve(base, lic)
        if candidate and candidate.exists() and candidate.is_file():
            license_file = candidate
        else:
            license_id = lic

    card = CardSpec(
        pretty_name=cblk.get("pretty_name"),
        description=cblk.get("description"),
        task_categories=list(cblk.get("task_categories") or ["automatic-speech-recognition"]),
        tags=list(cblk.get("tags") or []),
        size_categories=cblk.get("size_categories"),
        license_id=license_id,
        license_file=license_file,
        citation=cblk.get("citation"),
        contact=cblk.get("contact"),
        acknowledgments=cblk.get("acknowledgments"),
    )

    # ----- flows block -----
    flows_raw = raw.get("flows") or {}
    if not flows_raw:
        raise ValueError("Config has no `flows`. At least one flow is required.")
    flows: dict[str, FlowSpec] = {}
    for name, fblk in flows_raw.items():
        if not fblk or "record_zip" not in fblk:
            raise ValueError(f"flow '{name}' missing required `record_zip`")
        zip_path = _resolve(base, fblk["record_zip"])
        if not zip_path.exists():
            raise FileNotFoundError(f"record_zip not found: {zip_path}")
        fname_path, has_tx = _sniff_record_step(zip_path)
        flows[name] = FlowSpec(
            name=name,
            record_zip=zip_path,
            recording_check=_resolve(base, fblk.get("recording_check")),
            transcription_check=_resolve(base, fblk.get("transcription_check")),
            record_filename_path=fname_path,
            has_transcription_field=has_tx,
        )

    # ----- policies block -----
    # Policies are declared inline under `policies:` in the delivery YAML.
    # All keys are optional; unspecified ones fall back to Policies defaults.
    pblk = raw.get("policies") or {}
    if not isinstance(pblk, dict):
        raise ValueError(
            "`policies` must be an inline mapping in the delivery YAML "
            "(separate policy files are no longer supported)."
        )
    defaults = Policies()
    policies = Policies(
        include_pending=bool(pblk.get("include_pending", defaults.include_pending)),
        include_rejected=bool(pblk.get("include_rejected", defaults.include_rejected)),
        drop_offensive=bool(pblk.get("drop_offensive", defaults.drop_offensive)),
        duplicate_transcriptions=pblk.get(
            "duplicate_transcriptions", defaults.duplicate_transcriptions
        ),
        canonical_strategy=pblk.get(
            "canonical_strategy", defaults.canonical_strategy
        ),
    )

    # output_dir precedence: CLI arg > YAML `output_dir` > <config-dir>/out
    yaml_out = raw.get("output_dir")
    if output_dir:
        out = _resolve(base, output_dir)
    elif yaml_out:
        out = _resolve(base, yaml_out)
    else:
        out = base / "out"

    return Config(
        delivery=delivery,
        flows=flows,
        policies=policies,
        card=card,
        config_path=path,
        output_dir=out,
    )
