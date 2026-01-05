#!/bin/bash
# Quick QA run script

set -e

# Load environment if available
if [ -f .env ]; then
    source .env
fi

# Default values
AUDIO="${1:-}"
REQUEST="${2:-}"

if [ -z "$AUDIO" ] || [ -z "$REQUEST" ]; then
    echo "Usage: ./scripts/run_qa.sh <audio_path_or_gcs_uri> <request>"
    echo ""
    echo "Examples:"
    echo "  ./scripts/run_qa.sh gs://bucket/audio.mp3 'Create a podcast about AI'"
    echo "  ./scripts/run_qa.sh /local/audio.mp3 'Create a podcast about AI'"
    echo ""
    echo "Environment variables:"
    echo "  GOOGLE_AI_API_KEY    - Required for content evaluation"
    echo "  HUGGINGFACE_TOKEN    - Required for speaker diarization"
    echo "  GCP_PROJECT          - GCP project for GCS access"
    exit 1
fi

python -m kitesforu_qa.cli run \
    --audio "$AUDIO" \
    --request "$REQUEST" \
    --verbose
