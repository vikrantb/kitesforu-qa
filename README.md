# KitesForU Audio Quality Assurance (kqa)

A comprehensive 6-stage quality assurance system for podcast audio and content validation.

## Features

| Stage | What It Checks | Tool | Cost |
|-------|---------------|------|------|
| 1. Format | File validity, codec, duration | ffprobe | FREE |
| 2. Pronunciation | Word accuracy (WER) | Whisper + jiwer | FREE |
| 3. Audio Quality | Sound quality (MOS 1-5) | UTMOS/librosa | FREE |
| 4. Prosody | Naturalness, not robotic | librosa | FREE |
| 5. Content | Script quality | Gemini LLM | ~$0.001/script |
| 6. Voice Matching | Persona/gender match | Whisper + librosa | FREE |

**Total: $0-1.50/month** for production-grade QA.

## Installation

```bash
# From PyPI (when published)
pip install kitesforu-qa

# From source
git clone https://github.com/vikrantb/kitesforu-qa
cd kitesforu-qa
pip install -e .
```

### System Dependencies

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
apt-get install ffmpeg libsndfile1
```

## Quick Start

```bash
# Run QA on a local audio file
kqa run --audio /path/to/audio.mp3 --request "Create podcast about AI"

# Run QA on GCS file
kqa run --audio gs://bucket/audio.mp3 --request "Create podcast about AI"

# With script file for pronunciation check
kqa run --audio audio.mp3 --script script.txt --request "..."

# Check script before audio generation
kqa check-script --script script.txt --request "..." --duration 10
```

## CLI Commands

### `kqa run` - Run QA on Single Podcast

```bash
kqa run \
    --audio "gs://bucket/audio.mp3" \
    --request "Create a 10-minute podcast about AI" \
    --script /path/to/script.txt \
    --job-id "job_123" \
    --language en \
    --verbose \
    --output report.json
```

### `kqa e2e` - End-to-End Test

```bash
kqa e2e \
    --topic "The future of renewable energy" \
    --duration 10 \
    --api-url "https://api.kitesforu.com" \
    --wait-timeout 600
```

### `kqa batch` - Batch Processing

```bash
kqa batch \
    --input jobs.csv \
    --output results/ \
    --parallel 4
```

Input CSV format:
```csv
job_id,audio_path,request,language
job_123,gs://bucket/audio.mp3,"Create podcast about AI",en
job_456,gs://bucket/audio2.mp3,"Tech news",en
```

### `kqa check-script` - Pre-flight Script Check

```bash
kqa check-script \
    --script /path/to/script.txt \
    --request "Create podcast about AI" \
    --duration 10
```

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `GOOGLE_AI_API_KEY` | Gemini API key for content evaluation | For content eval |
| `HUGGINGFACE_TOKEN` | HuggingFace token for speaker diarization | Optional |
| `KITESFORU_API_KEY` | KitesForU API key for e2e tests | For e2e |
| `GCP_PROJECT` | GCP project for GCS access | For GCS |
| `WHISPER_MODEL` | Whisper model for transcription (default: large-v3) | Optional |
| `WHISPER_MODEL_FAST` | Whisper model for quick checks (default: base) | Optional |
| `LLM_MODEL` | Gemini model for content eval (default: gemini-2.0-flash) | Optional |

**Model Configuration Examples:**
```bash
# Use smaller Whisper model for faster local testing
export WHISPER_MODEL=base

# Use latest Gemini model
export LLM_MODEL=gemini-2.5-flash
```

### Thresholds

Default thresholds (configurable):
- **WER**: ≤10% word error rate
- **MOS**: ≥3.5 mean opinion score
- **Content**: ≥7.0 overall score
- **Pitch variation**: ≥20 Hz standard deviation

## Docker Usage

```bash
# Build image
cd docker
docker-compose build

# Run QA
docker-compose run kqa run \
    --audio /app/audio/sample.mp3 \
    --request "Create podcast about AI"
```

## Language Support

Currently optimized for:
- English (en)
- Spanish (es)
- French (fr)
- German (de)
- Hindi (hi)
- Japanese (ja)
- Chinese (zh)

```bash
kqa run --audio audio.mp3 --request "..." --language es
```

## Fleet Drift Sentinel (dark-feature detector)

`scripts/fleet_drift_sentinel.py` is the standing **dark-feature / fleet-drift detector**: a $0,
deterministic, read-only battery over `podcast_jobs` + `writeups` (Firestore field projections —
never full 1MiB docs) that compares the trailing 7 days against the prior 7 days and alerts on:

- **collapse of a GOOD signal** (CRITICAL, exit 1) — a feature the fleet used to produce went
  dark (prior >= 30% prevalence, >= 60% relative drop): motion clips (parallax/kenburns/video),
  music delivery, surfaced `video_url`, clip variety, writeup figures/SEO/sources, completion
  rate, cost rollups. Severity is **directionality-aware**: a BAD-signal family
  (`failure_reason:*` except none, `status:failed*`, `retried:*`, `gate:*`) collapsing is an
  IMPROVEMENT → INFO, never exit 1; those families **spiking** >= 2.5x is CRITICAL.
- **cost spikes are CRITICAL** — `cost_usd`/`credits_used` mean or p95 growing >= 2.5x (with
  volume) exits 1; a **single burned job** among ~70 (invisible to mean AND p95 — the honest
  dilution limit) surfaces via the max-vs-prior-p95 **outlier** channel (WARNING, non-gating).
- **applicability-aware denominators** — `motion:*`/`video_url_present` count only clip-bearing
  jobs; `clips_present` is the single mix-level signal, so audio-only/QA-campaign weeks
  (`wants_visuals: false`) can't fake a motion collapse.
- **expected-changes ack** — deliberate flips (flag off, feature removal, QA campaign) are muted
  to INFO via `scratch/reports/drift/ack.json`:
  `{"acks": [{"metric": "motion:*", "until": "2026-08-01", "segment": "short", "note": "…"}]}`
  (fnmatch metric globs; entries expire after their date; malformed file fail-opens to no acks).
- **gate-meta** (CRITICAL) — a QA gate axis failing >= 80% of gated jobs: the gate is punishing
  a symptom the pipeline manufactures (the 2026-07 motion incident: a 100%-failing visual gate
  was itself an unread alarm). Minimum-volume guards (>= 8 jobs/window/segment) everywhere.
- **transition + suspects** — each flagged metric is bisected to its first collapsed daily
  bucket and correlated against `gcloud run revisions list` deploy times (2-3 nearest suspects).
  `podcast_all` findings duplicating an identical short/episode finding are deduped —
  `podcast_all` only surfaces fleet-wide drift the per-segment volume guards would hide.

```bash
python3 scripts/fleet_drift_sentinel.py                  # 7d vs prior 7d → report + exit code
python3 scripts/fleet_drift_sentinel.py --once-baseline  # cold start: persist current battery
python3 scripts/fleet_drift_sentinel.py --quiet          # cron mode (files + exit code only)
```

**When to run:** once per deploy round — it exits 1 on any CRITICAL finding, so it can gate the
round — and it is cron/loop-schedulable for a standing watch (scheduling infra is a founder
follow-up). Reports land in `scratch/reports/drift/YYYY-MM-DD.{md,json}`. Detection logic is
pure-function and unit-tested with synthetic docs (`tests/test_fleet_drift_sentinel.py`); the
Firestore/gcloud readers are thin adapters. No LLM calls, no generation, no job mutation
(Tenet 9).

## Improvement Feedback (Beta)

When QA stages fail, the system generates actionable improvement feedback that can be used to create a feedback loop for continuous improvement.

### Feedback Structure

```json
{
  "improvement_feedback": {
    "has_suggestions": true,
    "priority": "high",
    "suggestion_count": 3,
    "suggestions": [
      {
        "stage": "content",
        "issue": "Script doesn't adequately cover the requested topic",
        "action": "Revise script generation prompt to focus on topic adherence",
        "component": "Script generation prompt",
        "priority": "high"
      }
    ],
    "improvement_prompt": "# Podcast Quality Improvement Recommendations...",
    "summary": "Found 3 improvement suggestions: 2 high, 1 medium priority"
  }
}
```

### Priority Levels

| Priority | Symbol | Description |
|----------|--------|-------------|
| Critical | 🔴 | Blocking issues - must fix immediately |
| High | 🟠 | Major quality issues - fix before release |
| Medium | 🟡 | Quality improvements - recommended |
| Low | 🟢 | Minor enhancements - optional |

### Using Feedback for Automation

The `improvement_prompt` field contains LLM-consumable text that can be passed to Claude or other AI systems to automatically improve podcast generation:

```python
from kitesforu_qa import QAPipeline

# Run QA
result = pipeline.run(audio_path="audio.mp3", user_request="...")

if not result.passed:
    feedback = result.get_improvement_feedback()

    # Pass to your improvement system
    improvement_prompt = feedback["improvement_prompt"]
    # Use this prompt with Claude/GPT to improve scripts, TTS settings, etc.
```

### Stage-Specific Improvements

The feedback system provides targeted improvements for each stage:

- **Format**: Sample rate, bitrate, duration issues
- **Pronunciation**: Word error rate, TTS model quality
- **Quality**: MOS score, audio clarity
- **Prosody**: Monotone voice, pitch variation
- **Content**: Topic coverage, structure, engagement
- **Voice Matching**: Placeholder names, gender/age matching

## Sample Output

```json
{
  "job_id": "job_abc123",
  "overall_passed": true,
  "stages": {
    "format": {
      "valid": true,
      "format": "mp3",
      "duration_seconds": 612,
      "passed": true
    },
    "pronunciation": {
      "word_error_rate": 0.032,
      "accuracy_percent": 96.8,
      "passed": true
    },
    "quality": {
      "mos_score": 4.23,
      "quality_level": "Good",
      "passed": true
    },
    "prosody": {
      "pitch_variation_hz": 45.2,
      "is_monotone": false,
      "passed": true
    },
    "content": {
      "overall_score": 7.8,
      "passed": true
    },
    "voice_matching": {
      "placeholder_detected": false,
      "gender_match": true,
      "passed": true
    }
  }
}
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run specific test
pytest tests/test_format.py -v

# Type checking
mypy src/
```

## Architecture

```
kitesforu-qa/
├── src/kitesforu_qa/
│   ├── cli.py              # CLI entry point
│   ├── config.py           # Configuration
│   ├── pipeline.py         # Orchestration
│   ├── stages/             # QA stages
│   │   ├── format.py       # Stage 1
│   │   ├── pronunciation.py# Stage 2
│   │   ├── quality.py      # Stage 3
│   │   ├── prosody.py      # Stage 4
│   │   ├── content.py      # Stage 5
│   │   └── voice_matching.py # Stage 6
│   ├── integrations/       # External services
│   │   ├── gcs.py
│   │   ├── kitesforu_api.py
│   │   └── llm.py
│   ├── models/             # Data models
│   └── utils/              # Utilities
├── tests/
└── docker/
```

## License

MIT
