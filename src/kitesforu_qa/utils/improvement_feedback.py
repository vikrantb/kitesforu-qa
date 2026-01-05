"""Generate improvement feedback targeting system components.

This module creates actionable feedback for Claude (the agent) to modify
the KitesForU system components: prompt templates, TTS configuration,
LLM model selection, and voice settings.

IMPORTANT: The user's request is FIXED - we never modify it.
We modify OUR internal system:
- Prompt templates in kitesforu-workers/src/workers/prompting/templates.py
- TTS service/model in kitesforu-workers/src/workers/stages/audio/tts_client.py
- Voice configuration and casting
- LLM model routing
"""

from typing import Any, Dict, List, Optional

# System component file paths (relative to kitesforu root)
WORKERS_BASE = "kitesforu-workers/src/workers"
TEMPLATES_FILE = f"{WORKERS_BASE}/prompting/templates.py"
TTS_CLIENT_FILE = f"{WORKERS_BASE}/stages/audio/tts_client.py"
AUDIO_WORKER_FILE = f"{WORKERS_BASE}/stages/audio/worker.py"
SCRIPT_WORKER_FILE = f"{WORKERS_BASE}/stages/script/worker.py"
PLANNER_FILE = f"{WORKERS_BASE}/stages/planner/worker.py"

# System component improvements mapped to QA issues
SYSTEM_IMPROVEMENTS = {
    "content": {
        "low_topic_score": {
            "issue": "Script doesn't adequately cover the requested topic",
            "target": {
                "type": "prompt_template",
                "file": TEMPLATES_FILE,
                "template_key": "script_generation",
                "section": "system"
            },
            "modification": {
                "type": "add_to_prompt",
                "lines": [
                    "CRITICAL: Every paragraph must directly relate to the user's topic.",
                    "Include specific examples, case studies, and data about the topic.",
                    "Define technical terms related to the topic."
                ]
            },
            "priority": "high"
        },
        "low_structure_score": {
            "issue": "Script lacks proper structure (intro/body/conclusion)",
            "target": {
                "type": "prompt_template",
                "file": TEMPLATES_FILE,
                "template_key": "script_generation",
                "section": "user"
            },
            "modification": {
                "type": "add_to_prompt",
                "lines": [
                    "REQUIRED STRUCTURE:",
                    "1. HOOK (30-60 sec) - Grab attention with surprising fact/question",
                    "2. INTRODUCTION - Set up what the episode covers",
                    "3. BODY - 3-5 clearly defined sections with transitions",
                    "4. CONCLUSION - Summarize key points + call-to-action"
                ]
            },
            "priority": "high"
        },
        "low_engagement_score": {
            "issue": "Script lacks engaging elements",
            "target": {
                "type": "prompt_template",
                "file": TEMPLATES_FILE,
                "template_key": "script_generation",
                "section": "system"
            },
            "modification": {
                "type": "add_to_prompt",
                "lines": [
                    "Make content engaging with:",
                    "- Rhetorical questions that make listeners think",
                    "- Personal anecdotes or relatable stories",
                    "- Direct address ('you', 'we') for connection",
                    "- Surprising facts or statistics"
                ]
            },
            "priority": "medium"
        },
        "low_speakability_score": {
            "issue": "Script contains sentences hard to speak naturally",
            "target": {
                "type": "prompt_template",
                "file": TEMPLATES_FILE,
                "template_key": "script_generation",
                "section": "system"
            },
            "modification": {
                "type": "add_to_prompt",
                "lines": [
                    "SPEAKABILITY RULES:",
                    "- Keep sentences under 20 words",
                    "- Avoid complex nested clauses",
                    "- Use contractions (don't, we're, it's)",
                    "- Write spoken word, not academic prose"
                ]
            },
            "priority": "medium"
        },
        "script_too_short": {
            "issue": "Script is too short for target duration",
            "target": {
                "type": "prompt_template",
                "file": TEMPLATES_FILE,
                "template_key": "script_generation",
                "section": "user"
            },
            "modification": {
                "type": "add_to_prompt",
                "lines": [
                    "WORD COUNT: Target ~150 words per minute of podcast.",
                    "For a {duration} podcast, write at least {word_count} words.",
                    "Develop each section fully with examples and explanations."
                ]
            },
            "priority": "high"
        },
        "script_too_long": {
            "issue": "Script is too long for target duration",
            "target": {
                "type": "prompt_template",
                "file": TEMPLATES_FILE,
                "template_key": "script_generation",
                "section": "user"
            },
            "modification": {
                "type": "add_to_prompt",
                "lines": [
                    "CONCISENESS: Stay within word count for target duration.",
                    "Be concise - each sentence should add value."
                ]
            },
            "priority": "medium"
        },
        "red_flags": {
            "issue": "Content quality issues detected by LLM review",
            "target": {
                "type": "prompt_template",
                "file": TEMPLATES_FILE,
                "template_key": "script_generation",
                "section": "system"
            },
            "modification": {
                "type": "add_to_prompt",
                "lines": [
                    "Quality check your output for:",
                    "- Factual accuracy",
                    "- No repetitive content",
                    "- Clear logical flow"
                ]
            },
            "priority": "high"
        }
    },
    "quality": {
        "low_mos": {
            "issue": "Audio quality (MOS) score is below threshold",
            "target": {
                "type": "tts_config",
                "file": TTS_CLIENT_FILE,
                "class": "TTSClient",
                "method": "generate_audio"
            },
            "modification": {
                "type": "change_config",
                "options": [
                    {
                        "change": "Use tts-1-hd model instead of tts-1",
                        "edit": {
                            "find": 'model: str = "tts-1"',
                            "replace": 'model: str = "tts-1-hd"'
                        }
                    },
                    {
                        "change": "Add ElevenLabs as TTS provider option",
                        "note": "ElevenLabs produces higher MOS but costs more"
                    }
                ]
            },
            "priority": "high"
        }
    },
    "prosody": {
        "monotone": {
            "issue": "Voice sounds monotone - lacks pitch variation",
            "target": {
                "type": "tts_config",
                "file": TTS_CLIENT_FILE,
                "class": "TTSClient"
            },
            "modification": {
                "type": "change_config",
                "options": [
                    {
                        "change": "Switch to more expressive voice",
                        "current": "alloy",
                        "recommended": "nova",
                        "reason": "nova has more natural pitch variation"
                    },
                    {
                        "change": "Add prosody markers to script generation",
                        "file": TEMPLATES_FILE,
                        "add": "Include [PAUSE], [EMPHASIS] markers in script"
                    }
                ]
            },
            "priority": "medium"
        },
        "flat_dynamics": {
            "issue": "Audio has flat dynamic range",
            "target": {
                "type": "tts_config",
                "file": TTS_CLIENT_FILE,
                "class": "TTSClient"
            },
            "modification": {
                "type": "change_config",
                "options": [
                    {
                        "change": "Use shimmer voice for better dynamics",
                        "reason": "shimmer is more expressive for dynamic content"
                    }
                ]
            },
            "priority": "medium"
        }
    },
    "pronunciation": {
        "high_wer": {
            "issue": "Word Error Rate too high - TTS mispronouncing words",
            "target": {
                "type": "tts_config",
                "file": TTS_CLIENT_FILE,
                "class": "TTSClient"
            },
            "modification": {
                "type": "change_config",
                "options": [
                    {
                        "change": "Use tts-1-hd for clearer pronunciation",
                        "reason": "HD model has better articulation"
                    },
                    {
                        "change": "Add phonetic hints to script generation",
                        "file": TEMPLATES_FILE,
                        "add": "For technical terms, add pronunciation guides"
                    }
                ]
            },
            "priority": "high"
        }
    },
    "voice_matching": {
        "placeholder_detected": {
            "issue": "Script contains placeholder names (Host 1, Speaker 2)",
            "target": {
                "type": "prompt_template",
                "file": TEMPLATES_FILE,
                "template_key": "podcast_outline"
            },
            "modification": {
                "type": "add_to_prompt",
                "lines": [
                    "IMPORTANT: Generate realistic host names for the topic.",
                    "Never use 'Host 1', 'Speaker 2', or placeholder names.",
                    "Match host name style to topic (tech = modern names, etc.)"
                ]
            },
            "priority": "high"
        },
        "gender_mismatch": {
            "issue": "Voice gender doesn't match host persona",
            "target": {
                "type": "voice_casting",
                "file": AUDIO_WORKER_FILE
            },
            "modification": {
                "type": "add_logic",
                "description": "Map script host gender to appropriate TTS voice",
                "note": "Ensure voice casting matches persona defined in script"
            },
            "priority": "medium"
        }
    },
    "format": {
        "low_sample_rate": {
            "issue": "Audio sample rate below optimal",
            "target": {
                "type": "audio_config",
                "file": AUDIO_WORKER_FILE
            },
            "modification": {
                "type": "verify_config",
                "check": "Ensure audio pipeline outputs at least 24kHz",
                "note": "OpenAI TTS outputs 24kHz by default - check processing"
            },
            "priority": "medium"
        },
        "low_bitrate": {
            "issue": "Audio bitrate too low",
            "target": {
                "type": "audio_config",
                "file": AUDIO_WORKER_FILE
            },
            "modification": {
                "type": "verify_config",
                "check": "Ensure MP3 output is at least 128kbps"
            },
            "priority": "medium"
        }
    }
}


def generate_improvement_feedback(
    qa_result: "QAResult",
    user_request: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate feedback targeting system components for Claude to modify.

    This tells Claude which FILES and CONFIGS to edit in kitesforu-workers,
    NOT what the user should do (user request is fixed).

    Args:
        qa_result: The QAResult object from QA pipeline
        user_request: Original user request (context only, never modified)

    Returns:
        Dictionary with system component modifications for Claude to apply
    """
    suggestions = []
    priority_scores = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    max_priority = 0

    for stage_name, stage_result in qa_result.stages.items():
        stage_suggestions = _analyze_stage(stage_name, stage_result)
        suggestions.extend(stage_suggestions)

        for s in stage_suggestions:
            p = priority_scores.get(s.get("priority", "low"), 0)
            max_priority = max(max_priority, p)

    priority_map = {4: "critical", 3: "high", 2: "medium", 1: "low", 0: "none"}
    overall_priority = priority_map.get(max_priority, "none")

    action_plan = _generate_action_plan(suggestions, user_request)

    return {
        "has_suggestions": len(suggestions) > 0,
        "priority": overall_priority,
        "suggestion_count": len(suggestions),
        "suggestions": suggestions,
        "action_plan": action_plan,
        "improvement_prompt": action_plan,  # Alias for compatibility
        "summary": _generate_summary(suggestions)
    }


def _analyze_stage(stage_name: str, stage_result: "StageResult") -> List[Dict[str, Any]]:
    """Analyze stage result and map to system component fixes."""
    suggestions = []
    stage_templates = SYSTEM_IMPROVEMENTS.get(stage_name, {})

    if not stage_result.passed:
        if stage_name == "content":
            suggestions.extend(_analyze_content_issues(stage_result, stage_templates))
        elif stage_name == "quality":
            suggestions.extend(_analyze_quality_issues(stage_result, stage_templates))
        elif stage_name == "prosody":
            suggestions.extend(_analyze_prosody_issues(stage_result, stage_templates))
        elif stage_name == "pronunciation":
            suggestions.extend(_analyze_pronunciation_issues(stage_result, stage_templates))
        elif stage_name == "voice_matching":
            suggestions.extend(_analyze_voice_issues(stage_result, stage_templates))
        elif stage_name == "format":
            suggestions.extend(_analyze_format_issues(stage_result, stage_templates))

    # Include stage issues as additional context
    if stage_result.issues:
        for issue in stage_result.issues:
            if not any(s.get("raw_issue") == issue for s in suggestions):
                suggestions.append({
                    "stage": stage_name,
                    "issue": issue,
                    "target": {"type": "review", "file": "varies"},
                    "modification": {"type": "investigate", "description": issue},
                    "priority": "medium",
                    "raw_issue": issue
                })

    return suggestions


def _analyze_content_issues(stage_result, templates) -> List[Dict]:
    suggestions = []
    data = stage_result.data or {}
    scores = data.get("scores", {})

    thresholds = {
        "topic": ("low_topic_score", 7),
        "structure": ("low_structure_score", 7),
        "engagement": ("low_engagement_score", 7),
        "speakability": ("low_speakability_score", 7)
    }

    for dimension, (template_key, threshold) in thresholds.items():
        score = scores.get(dimension, 10)
        if score < threshold:
            tpl = templates.get(template_key, {})
            if tpl:
                suggestions.append({
                    "stage": "content",
                    "dimension": dimension,
                    "current_score": score,
                    "threshold": threshold,
                    **tpl
                })

    red_flags = data.get("red_flags", [])
    if red_flags:
        tpl = templates.get("red_flags", {})
        if tpl:
            suggestions.append({
                "stage": "content",
                "red_flags": red_flags,
                **tpl
            })

    quick_issues = data.get("quick_check_issues", [])
    for issue in quick_issues:
        if "too short" in issue.lower():
            tpl = templates.get("script_too_short", {})
            if tpl:
                suggestions.append({"stage": "content", "raw_issue": issue, **tpl})
        elif "too long" in issue.lower():
            tpl = templates.get("script_too_long", {})
            if tpl:
                suggestions.append({"stage": "content", "raw_issue": issue, **tpl})

    return suggestions


def _analyze_quality_issues(stage_result, templates) -> List[Dict]:
    suggestions = []
    data = stage_result.data or {}

    mos = data.get("mos_score", 5.0)
    if mos < 3.5:
        tpl = templates.get("low_mos", {})
        if tpl:
            suggestions.append({
                "stage": "quality",
                "current_mos": mos,
                "threshold": 3.5,
                **tpl
            })

    return suggestions


def _analyze_prosody_issues(stage_result, templates) -> List[Dict]:
    suggestions = []
    data = stage_result.data or {}

    if data.get("is_monotone", False):
        tpl = templates.get("monotone", {})
        if tpl:
            suggestions.append({
                "stage": "prosody",
                "pitch_variation_hz": data.get("pitch_variation_hz"),
                **tpl
            })

    if data.get("is_flat", False):
        tpl = templates.get("flat_dynamics", {})
        if tpl:
            suggestions.append({"stage": "prosody", **tpl})

    return suggestions


def _analyze_pronunciation_issues(stage_result, templates) -> List[Dict]:
    suggestions = []
    data = stage_result.data or {}

    wer = data.get("word_error_rate", 0)
    if wer > 0.10:
        tpl = templates.get("high_wer", {})
        if tpl:
            suggestions.append({
                "stage": "pronunciation",
                "current_wer": f"{wer*100:.1f}%",
                **tpl
            })

    return suggestions


def _analyze_voice_issues(stage_result, templates) -> List[Dict]:
    suggestions = []
    data = stage_result.data or {}

    if data.get("placeholder_detected", False):
        tpl = templates.get("placeholder_detected", {})
        if tpl:
            suggestions.append({"stage": "voice_matching", **tpl})

    if not data.get("gender_match", True):
        tpl = templates.get("gender_mismatch", {})
        if tpl:
            suggestions.append({"stage": "voice_matching", **tpl})

    return suggestions


def _analyze_format_issues(stage_result, templates) -> List[Dict]:
    suggestions = []
    data = stage_result.data or {}

    if data.get("sample_rate", 48000) < 24000:
        tpl = templates.get("low_sample_rate", {})
        if tpl:
            suggestions.append({
                "stage": "format",
                "current_sample_rate": data.get("sample_rate"),
                **tpl
            })

    return suggestions


def _generate_action_plan(
    suggestions: List[Dict],
    user_request: Optional[str]
) -> str:
    """Generate action plan for Claude to modify system components."""
    if not suggestions:
        return "No system modifications needed. All QA checks passed."

    lines = [
        "# System Component Modifications Required",
        "",
        "Modify the kitesforu-workers codebase to fix these issues.",
        "NOTE: User request is FIXED - only modify system prompts/configs.",
        ""
    ]

    if user_request:
        lines.extend([
            f"**User Request (for context):** {user_request}",
            ""
        ])

    # Group by target file
    by_file = {}
    for s in suggestions:
        target = s.get("target", {})
        file_path = target.get("file", "unknown")
        if file_path not in by_file:
            by_file[file_path] = []
        by_file[file_path].append(s)

    for file_path, items in by_file.items():
        lines.append(f"## File: `{file_path}`")
        lines.append("")

        for item in items:
            priority = item.get("priority", "medium")
            emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(priority, "⚪")

            lines.append(f"### {emoji} {item.get('issue', 'Issue')}")

            target = item.get("target", {})
            if target.get("template_key"):
                lines.append(f"- **Template:** `{target['template_key']}`")
                lines.append(f"- **Section:** `{target.get('section', 'system')}`")

            mod = item.get("modification", {})
            if mod.get("type") == "add_to_prompt" and mod.get("lines"):
                lines.append("- **Add these lines to the prompt:**")
                lines.append("```")
                for line in mod["lines"]:
                    lines.append(line)
                lines.append("```")

            if mod.get("type") == "change_config" and mod.get("options"):
                lines.append("- **Configuration options:**")
                for opt in mod["options"]:
                    lines.append(f"  - {opt.get('change', 'Change')}")
                    if opt.get("reason"):
                        lines.append(f"    Reason: {opt['reason']}")
                    if opt.get("edit"):
                        edit = opt["edit"]
                        lines.append(f"    Find: `{edit.get('find')}`")
                        lines.append(f"    Replace: `{edit.get('replace')}`")

            lines.append("")

    lines.extend([
        "## Execution Steps",
        "",
        "1. Open files in `kitesforu-workers` repository",
        "2. Apply the modifications listed above",
        "3. Run tests: `cd kitesforu-workers && pytest`",
        "4. Deploy: `git add -A && git commit -m 'fix: improve prompts/configs per QA feedback'`",
        "5. Push triggers CI/CD: `git push origin main`",
        "6. Re-run QA to verify improvements",
        ""
    ])

    return "\n".join(lines)


def _generate_summary(suggestions: List[Dict]) -> str:
    """Generate summary of modifications needed."""
    if not suggestions:
        return "All quality checks passed. No system modifications needed."

    files_to_modify = set()
    by_priority = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    for s in suggestions:
        p = s.get("priority", "medium")
        by_priority[p] = by_priority.get(p, 0) + 1

        target = s.get("target", {})
        if target.get("file"):
            files_to_modify.add(target["file"].split("/")[-1])

    parts = []
    if by_priority["critical"]:
        parts.append(f"{by_priority['critical']} critical")
    if by_priority["high"]:
        parts.append(f"{by_priority['high']} high")
    if by_priority["medium"]:
        parts.append(f"{by_priority['medium']} medium")
    if by_priority["low"]:
        parts.append(f"{by_priority['low']} low")

    files_str = ", ".join(sorted(files_to_modify)) if files_to_modify else "config"
    return f"Modify {files_str}: {', '.join(parts)} priority"
