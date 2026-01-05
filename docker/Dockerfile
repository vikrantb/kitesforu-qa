# KitesForU QA Docker Image
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for caching
COPY pyproject.toml ./

# Install Python dependencies
RUN pip install --no-cache-dir build && \
    pip install --no-cache-dir .

# Download Whisper base model (for faster startup)
# Comment this out if you want to use large-v3 and download at runtime
RUN python -c "import whisper; whisper.load_model('base')" || true

# Copy application code
COPY src/ ./src/

# Install package in development mode
RUN pip install -e .

# Create temp directory
RUN mkdir -p /tmp/kitesforu-qa

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV KQA_TEMP_DIR=/tmp/kitesforu-qa

# Default entrypoint
ENTRYPOINT ["kqa"]
CMD ["--help"]
