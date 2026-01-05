"""Stage 5: Content quality evaluation using LLM-as-a-judge."""

import json
import re
from typing import List, Optional

from ..config import get_config
from ..models.results import StageResult


EVALUATION_PROMPT = """
You are evaluating a podcast script for quality.

USER'S ORIGINAL REQUEST:
{user_request}

GENERATED SCRIPT:
{script}

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


def evaluate_script_quality(
    user_request: str,
    script: str,
    language: str = "en",
    score_threshold: Optional[float] = None,
) -> StageResult:
    """
    Evaluate script quality using LLM-as-a-judge.

    Args:
        user_request: Original user request/topic
        script: Generated script text
        language: Language code
        score_threshold: Minimum overall score to pass

    Returns:
        StageResult with content evaluation results
    """
    config = get_config()
    if score_threshold is None:
        score_threshold = config.content_score_threshold

    if not config.enable_content_eval:
        return StageResult(
            stage_name="content",
            passed=True,
            data={"skipped": True, "reason": "Content evaluation disabled"},
        )

    # First run quick checks (free)
    quick_issues = quick_script_checks(script, target_duration_min=10)

    try:
        import google.generativeai as genai

        if not config.google_ai_api_key:
            return StageResult(
                stage_name="content",
                passed=False,
                error="GOOGLE_AI_API_KEY not configured",
            )

        genai.configure(api_key=config.google_ai_api_key)
        model = genai.GenerativeModel(config.llm_model)

        # Truncate script if too long
        max_script_len = 15000
        script_for_eval = script[:max_script_len] if len(script) > max_script_len else script

        prompt = EVALUATION_PROMPT.format(
            user_request=user_request,
            script=script_for_eval,
        )

        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.3}
        )

        # Parse response
        response_text = response.text.strip()

        # Remove markdown code blocks if present
        if response_text.startswith("```"):
            response_text = re.sub(r'^```\w*\n?', '', response_text)
            response_text = re.sub(r'\n?```$', '', response_text)

        evaluation = json.loads(response_text)

        # Combine LLM issues with quick check issues
        all_issues = quick_issues + evaluation.get("red_flags", [])

        overall_score = evaluation.get("overall_score", 0)
        passed = overall_score >= score_threshold and len(quick_issues) == 0

        return StageResult(
            stage_name="content",
            passed=passed,
            data={
                "scores": evaluation.get("dimensions", {}),
                "overall_score": overall_score,
                "red_flags": evaluation.get("red_flags", []),
                "quick_check_issues": quick_issues,
            },
            issues=all_issues if not passed else [],
        )

    except ImportError:
        return StageResult(
            stage_name="content",
            passed=False,
            error="google-generativeai not installed. Install with: pip install google-generativeai",
        )
    except json.JSONDecodeError as e:
        return StageResult(
            stage_name="content",
            passed=False,
            error=f"Failed to parse LLM response: {e}",
        )
    except Exception as e:
        return StageResult(
            stage_name="content",
            passed=False,
            error=f"Content evaluation failed: {e}",
        )


def quick_script_checks(
    script: str,
    target_duration_min: int = 10,
    words_per_minute: int = 150,
) -> List[str]:
    """
    Fast, free checks for obvious script problems.

    Args:
        script: Script text to check
        target_duration_min: Target duration in minutes
        words_per_minute: Estimated speaking rate

    Returns:
        List of issue strings
    """
    issues = []

    if not script or not script.strip():
        issues.append("Script is empty")
        return issues

    # Word count check
    words = len(script.split())
    expected_words = target_duration_min * words_per_minute

    if words < expected_words * 0.7:
        issues.append(
            f"Script too short: {words} words for {target_duration_min} min target "
            f"(expected ~{expected_words})"
        )
    if words > expected_words * 1.3:
        issues.append(
            f"Script too long: {words} words for {target_duration_min} min target "
            f"(expected ~{expected_words})"
        )

    # Sentence length check
    sentences = re.split(r'[.!?]+', script)
    long_sentences = [s for s in sentences if len(s.split()) > 35]
    if len(long_sentences) > 3:
        issues.append(
            f"{len(long_sentences)} sentences are too long to speak naturally (>35 words)"
        )

    # Has intro?
    intro_words = ['welcome', 'hello', 'today', "let's", 'going to', 'episode', 'podcast']
    first_500 = script[:500].lower()
    if not any(word in first_500 for word in intro_words):
        issues.append("Script may be missing a proper introduction")

    # Has outro?
    outro_words = ['thank', 'hope you', 'next time', 'goodbye', 'wrap up', 'conclusion', 'until next']
    last_500 = script[-500:].lower()
    if not any(word in last_500 for word in outro_words):
        issues.append("Script may be missing a proper conclusion")

    # Repetition check (same phrase repeated = LLM loop)
    words_list = script.lower().split()
    for i in range(len(words_list) - 10):
        phrase = ' '.join(words_list[i:i+5])
        count = script.lower().count(phrase)
        if count > 4 and len(phrase) > 20:
            issues.append(f"Repetitive phrase detected: '{phrase[:50]}...' appears {count} times")
            break

    return issues
