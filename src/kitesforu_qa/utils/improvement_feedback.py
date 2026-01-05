"""Generate improvement feedback prompts from QA results.

This module creates actionable improvement suggestions that can be used
by Claude or other LLMs to improve podcast generation pipelines.
"""

from typing import Any, Dict, List, Optional

# Stage-specific improvement templates
STAGE_IMPROVEMENTS = {
    "format": {
        "low_sample_rate": {
            "issue": "Audio sample rate is below optimal (< 24000 Hz)",
            "action": "Regenerate audio with sample_rate=24000 or higher",
            "component": "TTS configuration",
            "priority": "medium"
        },
        "low_bitrate": {
            "issue": "Audio bitrate is too low for quality playback",
            "action": "Use higher quality encoding (MP3 >= 128kbps, WAV >= 256kbps)",
            "component": "Audio encoding",
            "priority": "medium"
        },
        "duration_mismatch": {
            "issue": "Audio duration doesn't match expected length",
            "action": "Adjust script length or speaking rate in TTS settings",
            "component": "Script length / TTS speed",
            "priority": "high"
        },
        "invalid_format": {
            "issue": "Audio file format is invalid or corrupted",
            "action": "Check TTS output pipeline and file writing process",
            "component": "TTS output handling",
            "priority": "critical"
        }
    },
    "pronunciation": {
        "high_wer": {
            "issue": "Word Error Rate is too high - TTS is mispronouncing words",
            "action": "Use a higher quality TTS model or add phonetic hints to script",
            "component": "TTS model selection",
            "priority": "high"
        },
        "language_mismatch": {
            "issue": "Detected language doesn't match expected language",
            "action": "Ensure TTS voice matches script language",
            "component": "Voice/language selection",
            "priority": "high"
        }
    },
    "quality": {
        "low_mos": {
            "issue": "Audio quality (MOS) score is below acceptable threshold",
            "action": "Switch to a premium TTS provider or higher quality voice model",
            "component": "TTS provider/model",
            "priority": "high",
            "suggestions": [
                "Consider ElevenLabs for highest quality",
                "Use Google Cloud TTS Studio voices",
                "Try OpenAI TTS with 'hd' quality setting"
            ]
        }
    },
    "prosody": {
        "monotone": {
            "issue": "Voice sounds monotone - lacks pitch variation",
            "action": "Add more varied punctuation, questions, and emphasis markers to script",
            "component": "Script writing prompts",
            "priority": "medium",
            "script_hints": [
                "Add rhetorical questions to create pitch rises",
                "Include emphasis markers like *important* or CAPS",
                "Add emotional cues: (excited), (thoughtful), (serious)",
                "Vary sentence length for natural rhythm"
            ]
        },
        "flat_dynamics": {
            "issue": "Audio has flat dynamic range - no volume variation",
            "action": "Use a more expressive TTS model or add dynamic markers",
            "component": "TTS expression settings",
            "priority": "medium"
        }
    },
    "content": {
        "low_topic_score": {
            "issue": "Script doesn't adequately cover the requested topic",
            "action": "Revise script generation prompt to focus on topic adherence",
            "component": "Script generation prompt",
            "priority": "high",
            "prompt_additions": [
                "Ensure every paragraph connects back to the main topic",
                "Include specific examples and case studies",
                "Define key terms related to the topic"
            ]
        },
        "low_structure_score": {
            "issue": "Script lacks proper structure (intro/body/conclusion)",
            "action": "Add explicit structure requirements to script prompt",
            "component": "Script generation prompt",
            "priority": "high",
            "prompt_additions": [
                "Start with a compelling hook and topic introduction",
                "Organize content into 3-5 clear sections",
                "End with a summary and call-to-action"
            ]
        },
        "low_engagement_score": {
            "issue": "Script lacks engaging elements",
            "action": "Add storytelling and engagement requirements to prompt",
            "component": "Script generation prompt",
            "priority": "medium",
            "prompt_additions": [
                "Include relevant anecdotes and examples",
                "Add rhetorical questions to engage listeners",
                "Use conversational language and direct address"
            ]
        },
        "low_speakability_score": {
            "issue": "Script contains sentences that are hard to speak naturally",
            "action": "Add speakability constraints to script generation",
            "component": "Script generation prompt",
            "priority": "medium",
            "prompt_additions": [
                "Keep sentences under 25 words",
                "Avoid complex nested clauses",
                "Use natural spoken language, not written prose"
            ]
        },
        "script_too_short": {
            "issue": "Script is too short for target duration",
            "action": "Increase word count target in script generation",
            "component": "Script length parameters",
            "priority": "high",
            "calculation": "Target ~150 words per minute of podcast"
        },
        "script_too_long": {
            "issue": "Script is too long for target duration",
            "action": "Reduce word count target or increase target duration",
            "component": "Script length parameters",
            "priority": "medium"
        },
        "repetitive_content": {
            "issue": "Script contains repetitive phrases or ideas",
            "action": "Add diversity requirements to script generation prompt",
            "component": "Script generation prompt",
            "priority": "medium"
        },
        "red_flags": {
            "issue": "Content quality issues detected by LLM review",
            "action": "Address specific red flags in script revision",
            "component": "Script generation/review",
            "priority": "high"
        }
    },
    "voice_matching": {
        "placeholder_detected": {
            "issue": "Script contains placeholder names like 'Host 1' or 'Speaker 2'",
            "action": "Generate proper host names appropriate for the topic",
            "component": "Script generation - host naming",
            "priority": "high"
        },
        "gender_mismatch": {
            "issue": "Voice gender doesn't match the host name/persona",
            "action": "Ensure TTS voice gender matches script persona",
            "component": "Voice casting",
            "priority": "medium"
        },
        "age_mismatch": {
            "issue": "Voice age doesn't match topic expectations",
            "action": "Select voice persona appropriate for topic/audience",
            "component": "Voice casting",
            "priority": "medium"
        }
    }
}


def generate_improvement_feedback(
    qa_result: "QAResult",
    user_request: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate improvement feedback from QA results.

    Args:
        qa_result: The QAResult object from QA pipeline
        user_request: Original user request for context

    Returns:
        Dictionary with improvement suggestions and prompt
    """
    suggestions = []
    priority_scores = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    max_priority = 0

    # Analyze each stage for issues
    for stage_name, stage_result in qa_result.stages.items():
        stage_suggestions = _analyze_stage(stage_name, stage_result)
        suggestions.extend(stage_suggestions)

        # Track max priority
        for s in stage_suggestions:
            p = priority_scores.get(s.get("priority", "low"), 0)
            max_priority = max(max_priority, p)

    # Determine overall priority
    priority_map = {4: "critical", 3: "high", 2: "medium", 1: "low", 0: "none"}
    overall_priority = priority_map.get(max_priority, "none")

    # Generate LLM-consumable prompt
    improvement_prompt = _generate_prompt(suggestions, user_request, qa_result)

    return {
        "has_suggestions": len(suggestions) > 0,
        "priority": overall_priority,
        "suggestion_count": len(suggestions),
        "suggestions": suggestions,
        "improvement_prompt": improvement_prompt,
        "summary": _generate_summary(suggestions)
    }


def _analyze_stage(stage_name: str, stage_result: "StageResult") -> List[Dict[str, Any]]:
    """Analyze a stage result and generate suggestions."""
    suggestions = []
    stage_templates = STAGE_IMPROVEMENTS.get(stage_name, {})

    if not stage_result.passed:
        # Stage failed - identify specific issues
        if stage_name == "format":
            suggestions.extend(_analyze_format_issues(stage_result, stage_templates))
        elif stage_name == "pronunciation":
            suggestions.extend(_analyze_pronunciation_issues(stage_result, stage_templates))
        elif stage_name == "quality":
            suggestions.extend(_analyze_quality_issues(stage_result, stage_templates))
        elif stage_name == "prosody":
            suggestions.extend(_analyze_prosody_issues(stage_result, stage_templates))
        elif stage_name == "content":
            suggestions.extend(_analyze_content_issues(stage_result, stage_templates))
        elif stage_name == "voice_matching":
            suggestions.extend(_analyze_voice_issues(stage_result, stage_templates))

    # Also check for warnings even if passed
    if stage_result.issues:
        for issue in stage_result.issues:
            if not any(s.get("raw_issue") == issue for s in suggestions):
                suggestions.append({
                    "stage": stage_name,
                    "issue": issue,
                    "action": "Review and address the identified issue",
                    "priority": "medium",
                    "raw_issue": issue
                })

    return suggestions


def _analyze_format_issues(stage_result, templates):
    suggestions = []
    data = stage_result.data or {}

    if not data.get("valid", True):
        tpl = templates.get("invalid_format", {})
        suggestions.append({
            "stage": "format",
            **tpl
        })

    if data.get("sample_rate", 48000) < 24000:
        tpl = templates.get("low_sample_rate", {})
        suggestions.append({
            "stage": "format",
            "current_value": data.get("sample_rate"),
            **tpl
        })

    return suggestions


def _analyze_pronunciation_issues(stage_result, templates):
    suggestions = []
    data = stage_result.data or {}

    wer = data.get("word_error_rate", 0)
    if wer > 0.10:  # 10% threshold
        tpl = templates.get("high_wer", {})
        suggestions.append({
            "stage": "pronunciation",
            "current_value": f"{wer*100:.1f}%",
            "threshold": "10%",
            **tpl
        })

    return suggestions


def _analyze_quality_issues(stage_result, templates):
    suggestions = []
    data = stage_result.data or {}

    mos = data.get("mos_score", 5.0)
    if mos < 3.5:
        tpl = templates.get("low_mos", {})
        suggestions.append({
            "stage": "quality",
            "current_value": mos,
            "threshold": 3.5,
            **tpl
        })

    return suggestions


def _analyze_prosody_issues(stage_result, templates):
    suggestions = []
    data = stage_result.data or {}

    if data.get("is_monotone", False):
        tpl = templates.get("monotone", {})
        suggestions.append({
            "stage": "prosody",
            "pitch_variation": data.get("pitch_variation_hz"),
            **tpl
        })

    if data.get("is_flat", False):
        tpl = templates.get("flat_dynamics", {})
        suggestions.append({
            "stage": "prosody",
            "dynamic_range": data.get("dynamic_range_db"),
            **tpl
        })

    return suggestions


def _analyze_content_issues(stage_result, templates):
    suggestions = []
    data = stage_result.data or {}
    scores = data.get("scores", {})

    # Check individual dimension scores
    score_thresholds = {
        "topic": ("low_topic_score", 7),
        "structure": ("low_structure_score", 7),
        "engagement": ("low_engagement_score", 7),
        "speakability": ("low_speakability_score", 7)
    }

    for dimension, (template_key, threshold) in score_thresholds.items():
        score = scores.get(dimension, 10)
        if score < threshold:
            tpl = templates.get(template_key, {})
            suggestions.append({
                "stage": "content",
                "dimension": dimension,
                "current_score": score,
                "threshold": threshold,
                **tpl
            })

    # Check for red flags
    red_flags = data.get("red_flags", [])
    if red_flags:
        tpl = templates.get("red_flags", {})
        suggestions.append({
            "stage": "content",
            "red_flags": red_flags,
            **tpl
        })

    # Check quick check issues
    quick_issues = data.get("quick_check_issues", [])
    for issue in quick_issues:
        if "too short" in issue.lower():
            tpl = templates.get("script_too_short", {})
            suggestions.append({
                "stage": "content",
                "raw_issue": issue,
                **tpl
            })
        elif "too long" in issue.lower():
            tpl = templates.get("script_too_long", {})
            suggestions.append({
                "stage": "content",
                "raw_issue": issue,
                **tpl
            })
        elif "repet" in issue.lower():
            tpl = templates.get("repetitive_content", {})
            suggestions.append({
                "stage": "content",
                "raw_issue": issue,
                **tpl
            })

    return suggestions


def _analyze_voice_issues(stage_result, templates):
    suggestions = []
    data = stage_result.data or {}

    if data.get("placeholder_detected", False):
        tpl = templates.get("placeholder_detected", {})
        suggestions.append({
            "stage": "voice_matching",
            **tpl
        })

    if not data.get("gender_match", True):
        tpl = templates.get("gender_mismatch", {})
        suggestions.append({
            "stage": "voice_matching",
            "detected_gender": data.get("voice_gender", {}).get("gender"),
            **tpl
        })

    return suggestions


def _generate_prompt(
    suggestions: List[Dict],
    user_request: Optional[str],
    qa_result: "QAResult"
) -> str:
    """Generate an LLM-consumable improvement prompt."""
    if not suggestions:
        return "No improvements needed. All QA checks passed."

    lines = [
        "# Podcast Quality Improvement Recommendations",
        "",
        "Based on QA analysis, the following improvements are recommended:",
        ""
    ]

    if user_request:
        lines.extend([
            f"**Original Request:** {user_request}",
            ""
        ])

    # Group by component
    by_component = {}
    for s in suggestions:
        component = s.get("component", "General")
        if component not in by_component:
            by_component[component] = []
        by_component[component].append(s)

    for component, items in by_component.items():
        lines.append(f"## {component}")
        lines.append("")
        for item in items:
            priority = item.get("priority", "medium")
            priority_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(priority, "⚪")
            lines.append(f"- {priority_emoji} **{item.get('issue', 'Issue detected')}**")
            lines.append(f"  - Action: {item.get('action', 'Review and fix')}")

            if "prompt_additions" in item:
                lines.append("  - Suggested prompt additions:")
                for hint in item["prompt_additions"]:
                    lines.append(f"    - {hint}")

            if "script_hints" in item:
                lines.append("  - Script writing tips:")
                for hint in item["script_hints"]:
                    lines.append(f"    - {hint}")

            if "suggestions" in item:
                lines.append("  - Options to consider:")
                for opt in item["suggestions"]:
                    lines.append(f"    - {opt}")

            lines.append("")

    # Add actionable next steps
    lines.extend([
        "## Recommended Next Steps",
        ""
    ])

    # Prioritize by severity
    critical = [s for s in suggestions if s.get("priority") == "critical"]
    high = [s for s in suggestions if s.get("priority") == "high"]

    step_num = 1
    if critical:
        lines.append(f"{step_num}. **CRITICAL:** Address {len(critical)} critical issue(s) immediately")
        step_num += 1
    if high:
        lines.append(f"{step_num}. **HIGH:** Fix {len(high)} high-priority issue(s)")
        step_num += 1

    lines.append(f"{step_num}. Regenerate podcast with improvements applied")
    lines.append(f"{step_num + 1}. Re-run QA to verify improvements")

    return "\n".join(lines)


def _generate_summary(suggestions: List[Dict]) -> str:
    """Generate a brief summary of suggestions."""
    if not suggestions:
        return "All quality checks passed. No improvements needed."

    by_priority = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for s in suggestions:
        p = s.get("priority", "medium")
        by_priority[p] = by_priority.get(p, 0) + 1

    parts = []
    if by_priority["critical"]:
        parts.append(f"{by_priority['critical']} critical")
    if by_priority["high"]:
        parts.append(f"{by_priority['high']} high")
    if by_priority["medium"]:
        parts.append(f"{by_priority['medium']} medium")
    if by_priority["low"]:
        parts.append(f"{by_priority['low']} low")

    return f"Found {len(suggestions)} improvement suggestions: {', '.join(parts)} priority"
