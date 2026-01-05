#!/bin/bash
# Download required ML models

set -e

echo "Downloading Whisper models..."

# Download Whisper base model (fast, for voice matching)
python -c "import whisper; print('Downloading base model...'); whisper.load_model('base')"

# Optionally download large-v3 for pronunciation checking
# Uncomment if you want to pre-download (takes longer but better accuracy)
# python -c "import whisper; print('Downloading large-v3 model...'); whisper.load_model('large-v3')"

echo "Done!"
echo ""
echo "Note: large-v3 model will be downloaded on first use if not pre-downloaded."
echo "To download now, uncomment the line in this script."
