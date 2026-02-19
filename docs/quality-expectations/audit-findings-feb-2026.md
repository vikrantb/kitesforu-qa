# Content Quality Audit — February 2026

## Audit Scope
Full prompt and pipeline audit across all 4 repos: kitesforu-workers, kitesforu-course-workers,
kitesforu-api, and kitesforu-qa. Covers all content types (podcast, course, class, writeup)
and all pipeline stages (intake, research, syllabus, script, audio, writeup generation).

## Consolidated Issue Tracker

| # | Title | Severity | Repo | Status |
|---|-------|----------|------|--------|
| 1 | Research planner has no content-type awareness | CRITICAL | kitesforu-workers | Open |
| 2 | Class orchestrator hardcodes style="Explainer" | CRITICAL | kitesforu-course-workers | Open |
| 3 | Speaker system rigid Host1/Host2 hierarchy | HIGH | kitesforu-workers | Open |
| 4 | Emotion guidance is genre-blind | HIGH | kitesforu-workers | Open |
| 5 | Smart Create missing content category signal | HIGH | kitesforu-api | Open |
| 6 | Writeup research worker hallucinates sources | HIGH | kitesforu-course-workers | Open |
| 7 | Writeup format instructions are shallow | HIGH | kitesforu-course-workers | Open |
| 8 | Writeup voice calibration missing | MEDIUM | kitesforu-course-workers | Open |
| 9 | Class curriculum lacks emotional guardrails | MEDIUM | kitesforu-course-workers | Open |
| 10 | Batch vs Chat mode inconsistent | MEDIUM | kitesforu-api | Open |

---

## Issue Details

### Issue 1: Research Planner Has No Content-Type Awareness

**Severity**: CRITICAL
**Repo**: kitesforu-workers
**User Impact**: "I wanted to create romantic story and then it researched for recent news and events"

**Root Cause**:
1. `src/workers/stages/research/worker.py:722` — hardcodes `"tone": "professional"` for all content
2. `src/workers/stages/research/worker.py:706-725` — `_build_task_generation_prompt()` does NOT receive
   content_type, style, or any creative/fiction indicator
3. `src/workers/prompts/stages/research/task_planning.yaml:152` — "CRITICAL: When uncertain, use
   topic='news' with time_range='week'" causes creative content to get news research

**Files to Fix**:
- `src/workers/stages/research/worker.py` — Pass content_type to research planner, use style for tone
- `src/workers/prompts/stages/research/task_planning.yaml` — Add fiction override: always general for
  creative genres regardless of keywords
- `src/workers/services/prompt_generator/service.py` — Add content-type constraint to research prompt

**Suggested Fix**:
1. In research worker, accept content_type/style from message_data and pass to prompt
2. In task_planning.yaml, add rule: "IF content is fiction/creative, ALWAYS use topic='general'"
3. Use actual style (not hardcoded "professional") as tone parameter

---

### Issue 2: Class Orchestrator Hardcodes style="Explainer"

**Severity**: CRITICAL
**Repo**: kitesforu-course-workers
**User Impact**: All class lessons sound like educational explainers regardless of course style

**Root Cause**:
`src/workers/stages/class_orchestrator/audio_processor.py:40` — `style="Explainer"` hardcoded in
CreateJobRequest instead of using `class_params.get("style", "Explainer")`

**File to Fix**:
- `src/workers/stages/class_orchestrator/audio_processor.py:40`

**Fix**: Change `style="Explainer"` to `style=class_params.get("style", "Explainer")`

---

### Issue 3: Speaker System Rigid Host1/Host2 Hierarchy

**Severity**: HIGH
**Repo**: kitesforu-workers
**User Impact**: "it never has multiple speakers. sometimes when there is, it is the same guy talking"

**Root Cause**:
1. `src/workers/prompts/shared/speaker_rules.yaml:29-31` — Host1="main driver", Host2="curious one"
2. All dialogue formats use same rigid roles regardless of content type
3. No support for debate (equal authority), interview (guest), or character voices
4. Storytelling uses NARRATION format (single Narrator) instead of multi-voice

**Files to Fix**:
- `src/workers/prompts/shared/speaker_rules.yaml` — Add format-aware speaker roles
- `src/workers/prompts/stages/script/dialogue_generation.yaml` — Add debate/interview role options
- `src/workers/common/content_types.py` — Consider DIALOGUE for storytelling (not just NARRATION)

**Suggested Fix**:
1. Add content-type-specific speaker personality descriptions in speaker_rules.yaml
2. For debate content: both hosts have equal authority and opposing views
3. For storytelling: Host1=storyteller, Host2=engaged listener (already in romance rules)
4. Ensure Host1 and Host2 always use different TTS voices

---

### Issue 4: Emotion Guidance Is Genre-Blind

**Severity**: HIGH
**Repo**: kitesforu-workers
**User Impact**: Horror sounds documentary-like, comedy lacks wit, news sounds inappropriately excited

**Root Cause**:
1. `src/workers/prompts/shared/emotion_guidance.yaml` — Generic emotions (warm/excited/curious)
   applied uniformly to ALL content types
2. `src/workers/prompts/stages/script/monologue_generation.yaml:45-46` — Forces calm for ALL
   monologues including motivational content
3. No genre-specific emotion palettes (horror needs suspenseful/dread, comedy needs witty/comedic)
4. emotion_guidance.yaml included before content_type_guidance, unclear precedence

**Files to Fix**:
- `src/workers/prompts/shared/emotion_guidance.yaml` — Make genre-aware with genre-specific palettes
- `src/workers/prompts/stages/script/monologue_generation.yaml` — Add exceptions for non-meditative
- `src/workers/prompts/stages/script/dialogue_generation.yaml` — Clarify emotion precedence

**Suggested Fix**:
1. Add genre-specific emotion sections to emotion_guidance.yaml
2. Make monologue format detect content type and use appropriate emotion range
3. Add explicit "content_type_guidance emotions OVERRIDE generic emotions" instruction

---

### Issue 5: Smart Create Missing Content Category Signal

**Severity**: HIGH
**Repo**: kitesforu-api
**User Impact**: Creative content gets research-driven pipeline, no fiction/educational distinction

**Root Cause**:
1. `src/api/services/smart_create/intake.py:67-70` — Style enum conflates tone with content type
2. `src/api/services/smart_create/chat/prompts.py:99-145` — Promises research for ALL content
3. `src/api/services/smart_create/template_hints.yaml` — No processing_type metadata
4. `src/api/services/smart_create/executor.py:137-175` — Outline context lacks content type signal

**Files to Fix**:
- `template_hints.yaml` — Add processing_type: fiction|educational|news per template
- `chat/prompts.py` — Split capabilities by content type
- `executor.py` — Pass content category to workers via Pub/Sub message
- `intake.py` — Differentiate confidence scoring by content type

---

### Issue 6: Writeup Research Worker Hallucinates Sources

**Severity**: HIGH
**Repo**: kitesforu-course-workers
**User Impact**: Writeup "research" is LLM hallucination, no actual web search performed

**Root Cause**:
`src/workers/stages/writeup_research/worker.py` — Asks LLM to generate research synthesis
without executing any actual web search (Tavily, etc.). The LLM invents sources from training data.

**File to Fix**:
- `src/workers/stages/writeup_research/worker.py`

**Suggested Fix**: Integrate actual web search (Tavily) similar to podcast research pipeline,
or clearly document that writeup research is LLM synthesis (not verified research).

---

### Issue 7: Writeup Format Instructions Are Shallow

**Severity**: HIGH
**Repo**: kitesforu-course-workers
**User Impact**: Blog posts lack FAQ/SEO, newsletters wrong length, LinkedIn wrong units

**Root Cause**:
`src/workers/stages/writeup_generate/worker.py:31-90` — FORMAT_CONFIGS have generic instructions
that don't match platform-specific best practices.

**Specific Problems**:
- Blog: Missing FAQ section requirement, no keyword placement rules
- Newsletter: target_words=800 (should be 200-500), missing preheader, no email psychology
- LinkedIn: target_words=300 (should be target_chars=1400), no hook <210 chars rule
- Twitter: target_words=560 (Twitter counts chars, not words), needs per-tweet guidance
- Show Notes: Asks for timestamps even for standalone writeups (no podcast exists)

**File to Fix**:
- `src/workers/stages/writeup_generate/worker.py` — Rewrite FORMAT_CONFIGS

---

### Issue 8: Writeup Voice Calibration Missing

**Severity**: MEDIUM
**Repo**: kitesforu-course-workers

**Root Cause**: No EpisodeProfile or voice calibration passed to writeup generation.
All writeups sound the same regardless of topic/audience.

---

### Issue 9: Class Curriculum Lacks Emotional Guardrails

**Severity**: MEDIUM
**Repo**: kitesforu-course-workers

**Root Cause**: `src/workers/prompts/stages/class_syllabus/class_curriculum_generation.yaml`
lacks explicit instruction to avoid emotional tangents in K-12 content.

---

### Issue 10: Batch vs Chat Mode Inconsistent

**Severity**: MEDIUM
**Repo**: kitesforu-api

**Root Cause**: Batch mode (intake.py) is conservative about sufficiency, chat mode (prompts.py)
is aggressive. Same input produces different plans depending on entry point.

---

## Fix Priority

**Phase 1 (Critical — Fix Now)**:
1. Issue #1: Research planner content-type awareness (kitesforu-workers)
2. Issue #2: Class orchestrator style passthrough (kitesforu-course-workers)

**Phase 2 (High — Fix This Week)**:
3. Issue #4: Genre-aware emotion guidance (kitesforu-workers)
4. Issue #3: Speaker system flexibility (kitesforu-workers)
5. Issue #5: Smart Create content category (kitesforu-api)
6. Issue #7: Writeup format instructions (kitesforu-course-workers)

**Phase 3 (Medium — Fix Next Sprint)**:
7. Issue #6: Writeup research integrity
8. Issue #8: Writeup voice calibration
9. Issue #9: Class curriculum guardrails
10. Issue #10: Batch/chat consistency
