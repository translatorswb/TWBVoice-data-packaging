# TWB Voice data packaging

Turns raw voice-collection pipeline exports (audio zip + reviewer-verdict
JSONs) into a shippable delivery: enriched metadata, a Hugging-Face-style
dataset card, one combined tarball per delivery, and optional HF push.

Originally built for TWB Voice deliveries but project-agnostic — every
project-specific string (title, tags, license, citation, contact, etc.)
lives in the per-delivery YAML config, not the code.

## Quick start

```bash
pip install -e .                          # install
pip install -e ".[hf]"                    # also install HF push deps

# Run a delivery (sample example bundled in the repo)
twbvoice-pack run examples/sample-kanuri/config.yaml
```

Outputs land in `<config-dir>/out/` by default. Override per-config with a
top-level `output_dir:` in the YAML, or on the command line with `--out-dir`.

```
out/
├── kau_2026-04-23.tar.gz             one tarball per delivery
├── kau_2026-04-23_dataset_card.md    same card, also available outside the tarball
└── kau_2026-04-23_summary.md         counts and drops per flow
```

The tarball contains everything a partner needs:

```
<slug>/
├── audio/
│   ├── <flow>/
│   │   ├── approved/*.wav
│   │   ├── pending/*.wav
│   │   └── rejected/*.wav
│   └── …other flows…
├── metadata.json                     all rows across all flows; each row carries
│                                     `flow` and `audio_path` for direct lookup
├── dataset_card.md                   Hugging-Face-style card
├── README.txt                        partner-facing join instructions
└── LICENSE                           only if delivery.license points to a file
```

`audio_path` on each record is the path relative to `<slug>/`, e.g.
`audio/freeform/approved/698d4f55b4b05.wav` — so partners don't have to
reconstruct paths from filename + flow + status.

## Inputs

Drop the files produced by the pipeline (zips + validation JSONs) into a folder,
write a tiny YAML, run `twbvoice-pack run`. Minimum YAML:

```yaml
delivery:
  date: 2026-05-12
  language: { code: hau, name: Hausa }

flows:
  read:
    record_zip: raw/dataset_42-all-read_prompts_hau.zip
```

That's it. The script:

- Sniffs the record-step zip to determine where the filename lives (no manual config)
- Skips validation cleanly if `recording_check` / `transcription_check` are missing
- Supports one flow (read-only delivery) or many (e.g. spontaneous + read)
- Flow names are arbitrary — use `read`, `spontaneous`, `freeform`, whatever fits the delivery
- License is optional — SPDX string, path to a `LICENSE` file, or omit

A fuller example with both flows and inline policies lives at
`examples/sample-kanuri/config.yaml`. That example carries ~28 sampled rows
of real Kanuri metadata but **no audio bytes** — enough to exercise the
pipeline and inspect the output. For real deliveries, point `record_zip:`
at the audio-bearing zip from the source pipeline.

## Policies (inline in the delivery YAML)

```yaml
policies:
  include_pending:  true                       # ship rows not yet validated at the final step
  include_rejected: true                       # ship rejected rows (reasons are preserved in metadata)
  drop_offensive:   true                       # drop any row where any reviewer flagged offensive
  duplicate_transcriptions: all_mark_canonical # or: approved_only
  canonical_strategy:       verified_then_latest   # canonical = approved row, tie-break by latest created_at
```

Any key you omit falls back to the code default (matching the values above).
There is no separate policy file — keep it all in `config.yaml`.

Special rules that always apply (not configurable):

- PII rejections at the recording-check step are always dropped — the
  partner never sees them, regardless of policy.

## Dataset card

Everything that lands in the card comes from the `card:` block in the delivery
YAML — pretty_name, description, task_categories (default: ASR; switch to
text-to-speech or anything else), tags, license, citation, contact,
acknowledgments. Omit anything you don't need; sections are only rendered if
populated. The language code is auto-added as a tag.

```yaml
card:
  pretty_name: "Some voice corpus (2026-05-12)"
  description: |
    One paragraph about the dataset.
  task_categories: [automatic-speech-recognition]      # or [text-to-speech], etc.
  tags: [speech]
  size_categories: 10K<n<100K                          # auto-derived if omitted
  license: CC-BY-NC-4.0                                # SPDX id, or { file: ./LICENSE.txt }
  citation: |
    @misc{...}
  contact: |
    team@example.org
  acknowledgments: |
    Funded by ...
```

Auto-injected into the YAML frontmatter at build time: `download_size` (tarball
bytes), `dataset_size` (uncompressed audio + card bytes), and a sensible default
for `size_categories`.

Demographic distributions on the card are computed on **approved** recordings
only (matching HF convention). The "Hours by approval status" table shows the
full breakdown across approved / pending / rejected.

## Pushing to Hugging Face

```bash
export HF_TOKEN=hf_…
twbvoice-pack push-hf \
    --repo-id CLEAR-Global/twb-voice-kau-2026-04-23 \
    --out-dir examples/sample-kanuri/out
```

This extracts the tarball, lays out `data/<flow>/audio/*.wav` plus a Parquet
metadata file, copies the dataset card to `README.md`, and pushes. Use
`--dry-run` to stage everything without uploading. `--public` to create a
public repo (default is private).

> The HF push path has been written but has not yet been verified against a
> live HF org. First run should use `--dry-run`.

## Field semantics

The pipeline produces rows whose `hashed_user_id` refers to the *row owner*.
In `read` flows that's the speaker. In flows with a separate transcription
step (e.g. freeform / spontaneous), the row owner can be either the speaker
or a separate transcriber when multiple transcriptions exist for the same
audio. Per-audio statistics on the dataset card (hours, demographics) are
therefore computed only on the canonical row per file for transcription-bearing
flows.

## Repo layout

```
twbvoice-data-packaging/
├── pyproject.toml
├── README.md
├── src/twbvoice_pack/
│   ├── cli.py            entry point
│   ├── config.py         YAML schema + auto-sniffing
│   ├── enrich.py         joins record-step rows with reviewer verdicts
│   ├── policies.py       applies drop/keep rules and canonical tagging
│   ├── healthcheck.py    file integrity, durations, coverage
│   ├── analyze.py        hours/demographics/rejection-reason tallies
│   ├── card.py           renders dataset_card.md from a Jinja template
│   ├── tarball.py        per-delivery .tar.gz builder
│   └── hf_upload.py      `twbvoice-pack push-hf`
├── templates/dataset_card.md.j2
└── examples/sample-kanuri/               sampled end-to-end example (no audio)
```
