---
language:
  - kau
task_categories:
  - automatic-speech-recognition
size_categories:
  - n<1K
tags:
  - kau
  - speech
  - sample
pretty_name: "Sample Kanuri delivery (2026-04-23)"
download_size: 7670
dataset_size: 75865
---

# Sample Kanuri delivery (2026-04-23)

Sampled subset of a Kanuri voice delivery for the CLEAR Global TWB Voice
project. ~28 rows drawn from a production delivery to demonstrate the
metadata schema and pipeline behaviour. Audio is silent placeholder.


## Languages

- `kau` — Kanuri

## Dataset Structure

Audio files live under `audio/<flow>/<approval_status>/`. The combined `metadata.json` carries one row per recording (or per transcription, for flows with separate transcribers); each row exposes:

| field | description |
|---|---|
| `flow` | Flow name (e.g. `read`, `freeform`). |
| `audio_path` | Path to the WAV relative to the archive root. |
| `content.input.prompt` | Text shown to the speaker. |
| `content.answer.transcription` | Transcription (transcription-bearing flows only). |
| `content.answer.is_canonical_transcription` | True for the canonical transcription on rows with duplicates. |
| `content.answer.rejection_reasons` / `rejection_comment` / `reviewer_verdicts` | Recording-check verdict detail. |
| `content.answer.transcription_check` | Transcription-check verdict detail (transcription-bearing flows). |
| `content.metadata.user` | Self-reported demographics (year of birth, gender, country, education, language variant). |
| `approval_status` | `approved` / `pending` / `rejected`. |
| `hashed_user_id` | SHA-256 hash of the row-owner's user id. |
| `created_at` | Timestamp. |

## Statistics

### `freeform`

| metric | value |
|---|---:|
| Rows | 9 |
| Distinct audio files | 6 |
| Distinct speakers (approved) | 1 |
| Total hours (approved) | 0.02 |
| Approved / pending / rejected | 3 / 4 / 2 |
| Median / p95 duration (s) | 17.67 / 20.46 |

**Hours by approval status**

| status | hours |
|---|---:|
| approved | 0.02 |
| pending | 0.01 |

_Demographic distributions below are computed on approved recordings only._

**Hours by gender** (approved)

| gender | hours |
|---|---:|
| Male | 0.02 |

**Hours by age bucket** (approved)

| age | hours |
|---|---:|
| 30-44 | 0.02 |

**Speakers by gender** (approved)

| gender | speakers |
|---|---:|
| Male | 1 |

**Hours by country of origin** (approved)

| country | hours |
|---|---:|
| Nigeria | 0.02 |

**Hours by language variant** (approved)

| variant | hours |
|---|---:|
| Central | 0.02 |


**Transcription-check rejection reasons**

| reason | count |
|---|---:|
| Grammar or spelling issues | 2 |

### `read`

| metric | value |
|---|---:|
| Rows | 16 |
| Distinct audio files | 16 |
| Distinct speakers (approved) | 4 |
| Total hours (approved) | 0.03 |
| Approved / pending / rejected | 10 / 1 / 5 |
| Median / p95 duration (s) | 9.69 / 16.56 |

**Hours by approval status**

| status | hours |
|---|---:|
| approved | 0.03 |
| rejected | 0.02 |
| pending | 0.00 |

_Demographic distributions below are computed on approved recordings only._

**Hours by gender** (approved)

| gender | hours |
|---|---:|
| Female | 0.02 |
| Male | 0.01 |

**Hours by age bucket** (approved)

| age | hours |
|---|---:|
| 18-29 | 0.02 |
| 30-44 | 0.01 |

**Speakers by gender** (approved)

| gender | speakers |
|---|---:|
| Female | 2 |
| Male | 2 |

**Hours by country of origin** (approved)

| country | hours |
|---|---:|
| Saudi Arabia | 0.00 |
| Nigeria | 0.02 |

**Hours by language variant** (approved)

| variant | hours |
|---|---:|
| Central | 0.03 |

**Recording-check rejection reasons**

| reason | count |
|---|---:|
| Pronunciation issues or unnatural tone | 3 |
| Other voices can be heard in the background | 1 |
| The recording does not match the text or is incomplete | 1 |



## Quality Control

Recordings flow through up to four steps: record → recording-check → (optional) transcription → transcription-check. Each check step has up to 3 independent reviewers per item.

Policies applied to this delivery:

- Pending rows: kept
- Rejected rows: kept
- Offensive-flagged rows (any reviewer): dropped
- Duplicate transcriptions: all_mark_canonical (canonical selection: verified_then_latest)

Recordings flagged for PII at the recording-check step are always excluded.

## Caveats

- `freeform`: 7 of 9 rows (78%) reviewed by a single recording-check reviewer.
- `freeform`: 3 row(s) dropped due to offensive flag.
- `read`: 15 of 16 rows (94%) reviewed by a single recording-check reviewer.




