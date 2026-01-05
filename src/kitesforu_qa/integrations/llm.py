"""LLM integration for content evaluation."""

import json
import re
from typing import Any, Dict, Optional

from ..config import get_config


class LLMEvaluator:
    """LLM-based content evaluator using Gemini."""

    def __init__(self, model: Optional[str] = None):
        """
        Initialize evaluator.

        Args:
            model: Model name (default from config)
        """
        config = get_config()
        self.model_name = model or config.llm_model
        self.api_key = config.google_ai_api_key
        self._model = None

    @property
    def model(self):
        """Lazy-load the generative model."""
        if self._model is None:
            import google.generativeai as genai

            if not self.api_key:
                raise ValueError("GOOGLE_AI_API_KEY not configured")

            genai.configure(api_key=self.api_key)
            self._model = genai.GenerativeModel(self.model_name)

        return self._model

    def evaluate(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """
        Send prompt to LLM and get response.

        Args:
            prompt: Prompt text
            temperature: Sampling temperature
            max_tokens: Maximum response tokens

        Returns:
            Response text
        """
        response = self.model.generate_content(
            prompt,
            generation_config={
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
        )
        return response.text

    def evaluate_json(
        self,
        prompt: str,
        temperature: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Send prompt and parse JSON response.

        Args:
            prompt: Prompt text
            temperature: Sampling temperature

        Returns:
            Parsed JSON response
        """
        response_text = self.evaluate(prompt, temperature)

        # Clean up response
        text = response_text.strip()

        # Remove markdown code blocks if present
        if text.startswith("```"):
            text = re.sub(r'^```\w*\n?', '', text)
            text = re.sub(r'\n?```$', '', text)

        return json.loads(text)

    def evaluate_script(
        self,
        user_request: str,
        script: str,
        language: str = "en",
    ) -> Dict[str, Any]:
        """
        Evaluate podcast script quality.

        Args:
            user_request: Original user request
            script: Script text
            language: Language code

        Returns:
            Evaluation results
        """
        prompt = f"""
You are evaluating a podcast script for quality.

USER'S ORIGINAL REQUEST:
{user_request}

GENERATED SCRIPT:
{script[:15000]}

Evaluate the script on each dimension (1-10 scale):

1. TOPIC ADHERENCE: Does it actually cover what was requested?
2. STRUCTURE: Does it have a proper intro, logical flow, and conclusion?
3. ENGAGEMENT: Would this be interesting to listen to? Uses stories/hooks?
4. SPEAKABILITY: Are sentences short enough to speak naturally? No tongue-twisters?
5. ACCURACY: Do claims seem reasonable and grounded (not made up)?
6. STYLE MATCH: Does the tone match what the user wanted?

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "dimensions": {{
    "topic": <1-10>,
    "structure": <1-10>,
    "engagement": <1-10>,
    "speakability": <1-10>,
    "accuracy": <1-10>,
    "style": <1-10>
  }},
  "overall_score": <1-10 float>,
  "red_flags": [<list of critical issues or empty array>],
  "passed": <true/false based on overall_score >= 7>
}}
"""
        return self.evaluate_json(prompt)

    def check_voice_persona(
        self,
        transcript: str,
        expected_persona: str,
    ) -> Dict[str, Any]:
        """
        Check if voice matches expected persona.

        Args:
            transcript: Audio transcript
            expected_persona: Expected persona description

        Returns:
            Persona match results
        """
        prompt = f"""
Analyze if the speaker in this transcript matches the expected persona.

EXPECTED PERSONA:
{expected_persona}

TRANSCRIPT:
{transcript[:5000]}

Check for:
1. Vocabulary and speaking style match
2. Topic expertise consistency
3. Any persona breaks or inconsistencies

Respond ONLY with valid JSON:
{{
  "matches_persona": <true/false>,
  "confidence": <0-1 float>,
  "issues": [<list of issues or empty array>],
  "notes": "<brief explanation>"
}}
"""
        return self.evaluate_json(prompt)
