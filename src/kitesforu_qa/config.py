"""Configuration management for QA pipeline."""

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml


@dataclass
class QAConfig:
    """Configuration for QA pipeline."""

    # API Keys
    google_ai_api_key: str = field(
        default_factory=lambda: os.environ.get("GOOGLE_AI_API_KEY", "")
    )
    huggingface_token: str = field(
        default_factory=lambda: os.environ.get("HUGGINGFACE_TOKEN", "")
    )
    kitesforu_api_key: str = field(
        default_factory=lambda: os.environ.get("KITESFORU_API_KEY", "")
    )
    kitesforu_api_url: str = field(
        default_factory=lambda: os.environ.get(
            "KITESFORU_API_URL", "https://api.kitesforu.com"
        )
    )

    # GCS Configuration
    gcs_project: str = field(
        default_factory=lambda: os.environ.get("GCP_PROJECT", "kitesforu-dev")
    )

    # Model Settings
    whisper_model: str = "large-v3"
    whisper_model_fast: str = "base"  # For quick checks like voice matching
    llm_model: str = "gemini-2.0-flash"

    # Thresholds
    wer_threshold: float = 0.10  # 10% word error rate
    mos_threshold: float = 3.5
    content_score_threshold: float = 7.0
    pitch_std_min: float = 20.0
    dynamic_range_min: float = 4.0

    # Execution
    enable_diarization: bool = True
    enable_content_eval: bool = True
    verbose: bool = False

    # Temp directory for downloads
    temp_dir: str = field(
        default_factory=lambda: os.environ.get("KQA_TEMP_DIR", "/tmp/kitesforu-qa")
    )

    @classmethod
    def from_env(cls) -> "QAConfig":
        """Load configuration from environment variables."""
        return cls()

    @classmethod
    def from_file(cls, path: str) -> "QAConfig":
        """Load configuration from YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)

    def validate(self) -> list[str]:
        """Validate configuration and return list of issues."""
        issues = []

        if self.enable_content_eval and not self.google_ai_api_key:
            issues.append("GOOGLE_AI_API_KEY required for content evaluation")

        if self.enable_diarization and not self.huggingface_token:
            issues.append("HUGGINGFACE_TOKEN recommended for speaker diarization")

        return issues


# Global config instance
_config: Optional[QAConfig] = None


def get_config() -> QAConfig:
    """Get global config instance."""
    global _config
    if _config is None:
        _config = QAConfig.from_env()
    return _config


def set_config(config: QAConfig) -> None:
    """Set global config instance."""
    global _config
    _config = config
