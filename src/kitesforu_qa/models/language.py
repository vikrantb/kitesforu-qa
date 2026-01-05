"""Language configuration for multi-language QA support."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class Language(Enum):
    """Supported languages for QA."""
    ENGLISH = "en"
    SPANISH = "es"
    FRENCH = "fr"
    GERMAN = "de"
    HINDI = "hi"
    JAPANESE = "ja"
    CHINESE = "zh"
    KOREAN = "ko"
    PORTUGUESE = "pt"
    ARABIC = "ar"


# Language-specific name patterns for gender detection
LANGUAGE_NAME_PATTERNS: Dict[str, Dict[str, List[str]]] = {
    "en": {
        "female": ["Sara", "Sarah", "Emma", "Lisa", "Maria", "Rachel", "Jessica", "Amanda", "Emily", "Anna"],
        "male": ["John", "Mike", "David", "James", "Robert", "William", "Michael", "Daniel", "Chris", "Tom"],
    },
    "hi": {
        "female": ["Priya", "Kavita", "Sunita", "Neha", "Pooja", "Anita", "Meera", "Swati", "Divya", "Rani"],
        "male": ["Ram", "Raj", "Vikram", "Arjun", "Amit", "Rahul", "Suresh", "Ajay", "Vijay", "Rakesh"],
    },
    "es": {
        "female": ["Maria", "Carmen", "Ana", "Sofia", "Isabel", "Lucia", "Elena", "Rosa", "Paula", "Laura"],
        "male": ["Jose", "Carlos", "Miguel", "Juan", "Antonio", "Pedro", "Luis", "Diego", "Pablo", "Manuel"],
    },
    "fr": {
        "female": ["Marie", "Sophie", "Claire", "Emma", "Julie", "Camille", "Lea", "Manon", "Chloe", "Alice"],
        "male": ["Pierre", "Jean", "Michel", "Philippe", "Jacques", "Louis", "Henri", "Paul", "Marc", "Luc"],
    },
    "de": {
        "female": ["Anna", "Maria", "Emma", "Sophie", "Lena", "Julia", "Laura", "Lisa", "Sarah", "Nina"],
        "male": ["Hans", "Michael", "Thomas", "Stefan", "Klaus", "Peter", "Andreas", "Martin", "Frank", "Uwe"],
    },
}


# Language-specific prosody expectations (pitch standard deviation thresholds in Hz)
LANGUAGE_PROSODY_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "en": {"pitch_std_min": 20, "pitch_std_max": 80, "dynamic_range_min": 4},
    "es": {"pitch_std_min": 25, "pitch_std_max": 90, "dynamic_range_min": 5},
    "fr": {"pitch_std_min": 20, "pitch_std_max": 75, "dynamic_range_min": 4},
    "de": {"pitch_std_min": 18, "pitch_std_max": 70, "dynamic_range_min": 4},
    "hi": {"pitch_std_min": 25, "pitch_std_max": 85, "dynamic_range_min": 5},
    "ja": {"pitch_std_min": 30, "pitch_std_max": 100, "dynamic_range_min": 4},
    "zh": {"pitch_std_min": 40, "pitch_std_max": 120, "dynamic_range_min": 5},  # Tonal language
    "ko": {"pitch_std_min": 25, "pitch_std_max": 90, "dynamic_range_min": 4},
    "pt": {"pitch_std_min": 25, "pitch_std_max": 85, "dynamic_range_min": 5},
    "ar": {"pitch_std_min": 20, "pitch_std_max": 80, "dynamic_range_min": 4},
}


@dataclass
class LanguageConfig:
    """Language-specific QA configuration."""
    language: Language
    whisper_model: str = "large-v3"
    whisper_task: str = "transcribe"  # or "translate" for English output
    name_patterns: Optional[Dict[str, List[str]]] = None
    prosody_thresholds: Optional[Dict[str, float]] = None

    def __post_init__(self):
        """Initialize language-specific patterns after dataclass init."""
        if self.name_patterns is None:
            self.name_patterns = LANGUAGE_NAME_PATTERNS.get(
                self.language.value,
                LANGUAGE_NAME_PATTERNS["en"]  # Default to English
            )
        if self.prosody_thresholds is None:
            self.prosody_thresholds = LANGUAGE_PROSODY_THRESHOLDS.get(
                self.language.value,
                LANGUAGE_PROSODY_THRESHOLDS["en"]  # Default to English
            )
