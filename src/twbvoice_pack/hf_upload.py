"""Push a built delivery to a Hugging Face dataset repository.

Layout on HF:
    data/<flow>/<split>-00000-of-00001.parquet

`<flow>` becomes a HuggingFace dataset **config** (e.g. `read`, `freeform`).
`<split>` is one of `train`, `dev`, `test`, `rejected`, `pending`:

    train / dev / test  — approved rows, deterministically hash-split 80/10/10
                          on the audio filename (reproducible across runs).
    rejected            — rows with approval_status == "rejected".
    pending             — rows with approval_status == "pending".

Audio bytes are embedded into the Parquet under an `audio` column with the
schema HuggingFace's Audio feature expects (`{"bytes": ..., "path": ...}`).
This means the HF dataset viewer renders the audio inline.

The README.md uploaded to the repo is the dataset card produced by
`twbvoice-pack run`, with the HF `configs:` block injected into the YAML
frontmatter so the viewer knows about the subsets/splits.

Requirements:
    pip install "twbvoice-data-packaging[hf]"
    export HF_TOKEN=...           # or `huggingface-cli login` first
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
from collections import defaultdict
from pathlib import Path


SPLITS_ORDER = ("train", "dev", "test", "rejected", "pending")


def _import_hf():
    try:
        from huggingface_hub import HfApi, create_repo
        import pandas as pd
    except ImportError as e:
        sys.exit(
            "Missing optional deps. Install with: "
            'pip install "twbvoice-data-packaging[hf]"\n'
            f"({e})"
        )
    return HfApi, create_repo, pd


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _find_tarball(out_dir: Path) -> Path:
    tarballs = sorted(out_dir.glob("*.tar.gz"))
    if not tarballs:
        sys.exit(f"No *.tar.gz found in {out_dir}")
    if len(tarballs) > 1:
        sys.exit(
            f"Multiple *.tar.gz in {out_dir}: pick one and rename/move the others.\n"
            f"Found: {[t.name for t in tarballs]}"
        )
    return tarballs[0]


def _split_for(filename: str, approval_status: str) -> str:
    """Approved rows → train/dev/test via stable hash. Otherwise → status name."""
    if approval_status == "approved":
        h = int(hashlib.sha256(filename.encode()).hexdigest(), 16) % 100
        if h < 80:
            return "train"
        if h < 90:
            return "dev"
        return "test"
    return approval_status or "unknown"


def _flatten(r: dict) -> dict:
    inp = r["content"].get("input") or {}
    ans = r["content"].get("answer") or {}
    u = (r["content"].get("metadata") or {}).get("user") or {}
    return {
        "id": r.get("id"),
        "task_id": r.get("task_id"),
        "hashed_user_id": r.get("hashed_user_id"),
        "flow": r.get("flow"),
        "filename": inp.get("filename") or ans.get("filename"),
        "duration": inp.get("duration") or ans.get("duration"),
        "prompt": inp.get("prompt"),
        "transcription": ans.get("transcription"),
        "is_canonical_transcription": ans.get("is_canonical_transcription"),
        "approval_status": r.get("approval_status"),
        "rejection_reasons": ans.get("rejection_reasons") or [],
        "rejection_comment": ans.get("rejection_comment"),
        "year_of_birth": u.get("year_of_birth"),
        "gender": u.get("gender"),
        "country_of_origin": u.get("country_of_origin"),
        "education_level": u.get("education_level"),
        "language_variant": u.get("language_variant"),
        "created_at": r.get("created_at"),
    }


def _load_audio_bytes(tar_path: Path, slug: str) -> dict[str, bytes]:
    """Read all WAV files from the tarball into memory, keyed by basename."""
    out: dict[str, bytes] = {}
    with tarfile.open(tar_path) as tf:
        for m in tf.getmembers():
            if not m.isfile() or not m.name.endswith(".wav"):
                continue
            f = tf.extractfile(m)
            if f is None:
                continue
            out[m.name.split("/")[-1]] = f.read()
    return out


def _build_groups(records: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Group records by (flow, split)."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in records:
        flow = r.get("flow") or "unknown"
        status = r.get("approval_status") or "unknown"
        # Filename is at either input.filename (freeform) or answer.filename (read).
        fn = (
            (r["content"].get("input") or {}).get("filename")
            or (r["content"].get("answer") or {}).get("filename")
        )
        split = _split_for(fn or "", status)
        groups[(flow, split)].append(r)
    return groups


def _inject_configs_frontmatter(card_md: str, groups: dict[tuple[str, str], list[dict]]) -> str:
    """Insert a `configs:` block into the card's YAML frontmatter."""
    flows = sorted({flow for (flow, _) in groups.keys()})
    lines: list[str] = ["configs:"]
    for flow in flows:
        present_splits = {sp for (fl, sp) in groups.keys() if fl == flow}
        ordered_splits = [s for s in SPLITS_ORDER if s in present_splits]
        if not ordered_splits:
            continue
        lines.append(f"- config_name: {flow}")
        lines.append("  data_files:")
        for sp in ordered_splits:
            lines.append(f"  - split: {sp}")
            lines.append(f"    path: data/{flow}/{sp}-*.parquet")
    configs_block = "\n".join(lines)

    # Insert before the closing --- of the frontmatter.
    m = re.match(r"^(---\n.*?\n)(---\n)", card_md, re.DOTALL)
    if not m:
        # No frontmatter — wrap one.
        return f"---\n{configs_block}\n---\n\n{card_md}"
    head, end = m.group(1), m.group(2)
    return head + configs_block + "\n" + end + card_md[m.end():]


def _write_parquets(
    groups: dict[tuple[str, str], list[dict]],
    audio: dict[str, bytes],
    repo_root: Path,
    pd,
) -> dict[tuple[str, str], int]:
    """Build one Parquet per (flow, split). Returns sizes per group."""
    sizes: dict[tuple[str, str], int] = {}
    for (flow, split), rows in groups.items():
        records_flat = []
        for r in rows:
            base = _flatten(r)
            fn = base["filename"]
            audio_blob = audio.get(fn)
            base["audio"] = (
                {"bytes": audio_blob, "path": fn} if audio_blob is not None else None
            )
            records_flat.append(base)
        df = pd.DataFrame(records_flat)
        target = repo_root / "data" / flow
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{split}-00000-of-00001.parquet"
        df.to_parquet(path, index=False)
        sizes[(flow, split)] = path.stat().st_size
    return sizes


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def push(
    repo_id: str,
    out_dir: Path,
    *,
    private: bool = True,
    dry_run: bool = False,
) -> int:
    HfApi, create_repo, pd = _import_hf()

    tar_path = _find_tarball(out_dir)
    print(f"  source tarball:  {tar_path.name} ({tar_path.stat().st_size / 1e9:.2f} GB)")
    print(f"  target repo:     {repo_id}  (visibility: {'public' if not private else 'private'})")

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # Read metadata.json + card from the tarball without extracting audio twice.
        with tarfile.open(tar_path) as tf:
            members = {m.name: m for m in tf.getmembers()}
            slug = sorted({n.split("/", 1)[0] for n in members})[0]
            with tf.extractfile(members[f"{slug}/metadata.json"]) as f:
                records = json.load(f)
            card_md = tf.extractfile(members[f"{slug}/dataset_card.md"]).read().decode("utf-8")

        # Build (flow, split) groups
        groups = _build_groups(records)
        print(f"  records:         {len(records)}")
        print(f"  groups (flow, split):")
        for k in sorted(groups.keys()):
            print(f"    {k[0]:<12} {k[1]:<10} {len(groups[k]):>6} rows")

        # Read audio bytes (one full pass)
        print("  loading audio bytes from tarball…")
        audio = _load_audio_bytes(tar_path, slug)
        print(f"  loaded {len(audio)} wav files into memory")

        # Stage repo layout
        repo_root = td / "_hf_repo"
        repo_root.mkdir()
        sizes = _write_parquets(groups, audio, repo_root, pd)
        for (flow, split), nbytes in sorted(sizes.items()):
            print(f"    data/{flow}/{split}-*.parquet  {nbytes / 1e6:.1f} MB")

        # README with injected configs:
        readme_md = _inject_configs_frontmatter(card_md, groups)
        (repo_root / "README.md").write_text(readme_md)

        # Free the in-memory audio dict so HF upload doesn't double the footprint.
        audio.clear()

        total = sum(p.stat().st_size for p in repo_root.rglob("*") if p.is_file())
        print(f"  total upload size: {total / 1e9:.2f} GB")

        if dry_run:
            print("  (dry run — not creating repo or uploading)")
            for p in sorted(repo_root.rglob("*")):
                if p.is_file():
                    print(f"    {p.relative_to(repo_root)}  ({p.stat().st_size} B)")
            return 0

        create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True)
        api = HfApi(token=os.environ.get("HF_TOKEN"))
        api.upload_folder(
            folder_path=str(repo_root),
            repo_id=repo_id,
            repo_type="dataset",
            commit_message=f"upload {slug}",
        )
        print(f"  uploaded → https://huggingface.co/datasets/{repo_id}")
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--repo-id", required=True)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--public", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    return push(args.repo_id, args.out_dir, private=not args.public, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
