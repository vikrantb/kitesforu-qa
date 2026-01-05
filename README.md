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
