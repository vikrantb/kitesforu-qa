# Debug DAG Tool — Deep-Design Output (39-agent workflow wgjo04y7b)

_Generated design reference: exhaustive data manifest + build/interaction spec for `trace_job.py`._


<!-- ===== manifest ===== -->

# CONSOLIDATED DATA MANIFEST — Debug DAG Tool

Reference job: `podcast_jobs/0994ebdc-7c01-4a68-9f56-7c500bed2df2` (non-fiction 5-min AWS-cert explainer; narration/solo). All paths are on that Firestore doc unless prefixed `gs://` (GCS) or `LLM` (llm_call_logs entry). De-duplicated: each datum is owned by exactly one node; cross-slice repeats are collapsed and noted in §11 (Shared fields) and §10 (Edges).

## Legend
- **Source**: `FS`=top-level job-doc field · `FS.`=literal dotted top-level key (NOT nested) · `FS[nested]`=inside `preferences`/`inputs`/`stages` · `GCS`=debug-artifacts object · `LLM`=llm_call_logs[] (+ optional GCS) · `SUB`=separate Firestore collection · `DERIVED`=computed, not persisted
- **Drill**: deepest UI level the datum needs — `L0`=node face/badge · `L1`=hover essence · `L2`=click panel · `L3`=deep (raw JSON / LLM prompt+response / GCS fetch)
- **Now**: Y=surfaced today · P=partial/raw-dump-only · N=invisible
- GCS artifact prefix for all L3 LLM fetches: `gs://kitesforu-dev-podcasts/debug-artifacts/<job_id>/{llm_response,llm_system_prompt,llm_user_prompt}/*.json`, addressed via `llm_call_logs[].{system_prompt_gcs_path,user_prompt_gcs_path,response_gcs_path}` (lazy-fetch on L3; fall back to `response_preview` when `*_gcs_path=None`).

---

## NODE GROUP A — INTAKE & PLAN (DAG root cluster; everything descends from here)

### A1. IDEA
| Field (path) | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `inputs.smart_create_context` (verbatim raw idea) | FS. (dotted) | **N** | L2 | **#1 gap.** Not the nested `inputs` map the page renders. Hover=char count+mode. |
| `inputs` (topic, style, quality, duration_min, language, mode, skip_clarifier, full_control, custom_instructions, priority, intro_enabled, audience, notify_email) | FS[nested] | Y | L2 | Flat table today. |
| `inputs.{sub_mode, music_enabled, audio_overview_mode, source_material}` | FS[nested] | P | L2 | Dotted sub-inputs. |
| `inputs.custom_instructions` | FS[nested] | Y | L1 | **== top-level `smart_create_context`** — flag as same string, don't double-show. |

### A2. CLARIFIER-GATE
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `clarifier` (questions, answers, confidence) | FS | N | L2 | Null here (bypassed). |
| `inputs.skip_clarifier` | FS[nested] | N* | L1 | true → gate bypassed. |
| `awaiting_user_action` | FS | N | L1 | Park-state when gate/guided-research pauses (shared w/ D). |
| `plan.assumptions` (≤3 × ≤80 chars) | FS | N | L2 | Inline assumptions streamed when bypassed. |

### A3. PLAN / OUTLINE
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `smart_create_outline[]` {name, description, source_ref?} | FS. | **N** | L3 | **The plan spine** (5 items). No GCS artifact — Firestore-only. Per-item click → source_ref. |
| `content_purpose` | FS. | N | **L0** | **Branches the whole DAG** (podcast/course/class/writeup/corporate_training). |
| `content_category` | FS. | N | L1 | educational. Feeds educational-solo coercion. |
| `content_domain` | FS. | N | L1 | cloud-devops. Membership in diagram-heavy set drives narration coercion. |
| `style / format / story_mode / truth_contract` | FS | P | L1 | Chips; null story_mode here. |

### A4. COMPOSED-BRIEF
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `smart_create_context` (outline-approval wrapper) | FS. | Y | L2 | The exact text workers ground on; Firestore-only (no GCS). Mark as derived from A1+A3. |
| `personalization.listener_tier` | FS. | N | L1 | resident. Routes scaffolding depth. |

### A5. CONTENT-SPEC (flag `ENABLE_CONTENT_SPEC`)
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `content_spec` {platforms, goal, audience, tone, hook_style, ending_mode, cta, brand} | FS. | N | L2 | platforms=['youtube']. Drives multi-platform fan-out edges. Render ABSENT gracefully. |

### A6. CREATIVE-INTENT (authored in research stage — draw cross-stage edge)
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `creative_intent` {chosen_angle, emotional_truth_line, viewer{…}} + `preferences._creative_intent` | FS. + FS[nested] | Y | L2 | Shown in DirectorsBrainPanel, disconnected from idea/outline. `preferences._creative_intent` is the copy the script composer actually reads. |
| `creative_intent.viewer.{persona, prior_knowledge, unvoiced_objection, secret_want, emotional_need, fascinating_adjacent_angle}` | FS. | Y | L2 | Priya. |
| LLM: `angle_tournament_candidates`, `angle_tournament_judge` (2) | LLM+GCS | P | L3 | gpt-5-mini. Winner→chosen_angle. |
| `creative_intent_cache/{content_hash}` | SUB | N | L3 | Cross-job cache; hit ⇒ intent NOT freshly authored. |

### A7. ANGLE-BRIEF (authored in research stage)
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `angle_brief` {final_angle, director_log[4], lens_notes{teacher,skeptic,contrarian,storyteller}} | FS. | **N** | L3 | The 4-lens deliberation behind the angle; per-lens click. Diff final_angle vs creative_intent.chosen_angle. |
| LLM: `angle_trust_lens_{teacher,contrarian,skeptic,storyteller}`, `angle_trust_synthesis`, `skeptical_friend_critique`, `skeptical_friend_regen` (7) | LLM+GCS(partial) | N | L3 | 4 lens + judge + 2 skeptical-friend calls have `response_gcs_path=None` → use response_preview. |

### A8. SCENARIO-TAILORING (bridge to research/visuals/script)
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `scenario_tailoring` {content_subtype, modality_policy, visual_directive{allow_photoreal,min_medium,density,maturity}, needs_research, tone, recency, time_range, reason} | FS. | N | L2 | modality_policy=authored_diagrams; needs_research=false. |

---

## NODE GROUP B — DISCOVERY / EPISODE PROFILE (feeds routing + planner + architect; owns the format lock)
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `episode_profile` (27 keys: topic, genre, format, structure, tone, pacing, speech_patterns, vocabulary_guidance, cultural_context, audience_level, audience_adaptation, emotional_arc, speaker_count, speaker_roles, research_angles, duration_minutes, language, user_preferences, …) | FS. | P (.genre only) | L2 | Full card. |
| `episode_profile.skip_research` | FS. | Y | L1 | **The actual dispatch driver** (not stages.*.route). |
| `episode_profile.story_engine` {primary_engine, truth_contract, locks, closing_image_type, emotional_lens, source, legacy_genre_str} | FS. | N | L2 | Taxonomy spine. truth_contract='grounded' lives here (null at top level). |
| `episode_profile.discovery_reasoning{}` (per-field WHY) | FS. | N | L3 | Only structured rationale for tone/format/genre. |
| `episode_profile.raw_discovery_response{}` | FS. | N | L3 | Verbatim LLM JSON. Discovery call (gpt-4o-mini, 6661ms) is **NOT in llm_call_logs & has no GCS artifact** — this + discovery_log are its only trace. |
| `episode_profile_discovery_log` (26 keys) | FS. | P (.genre_source) | L2 | Health: fallback_used, error, discovery_model, discovery_duration_ms. |

---

## NODE GROUP C — JOB-INITIATE / ROUTING (first pipeline node)

**Lifecycle & cost**
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `stages['job-initiate']` {status, started_at, completed_at, result{budget,topic,preferences,…}} | FS[stages] | Y | L2 | |
| `current_stage / status / created_at / updated_at` | FS | Y | L0 | |
| `inputs.language` (+ detected override) | FS[nested] | Y | L1 | detect_and_override_language. |
| `allow_premium` | FS | Y | L1 | **Premium source-of-truth (bool).** |
| `quality_tier` | FS | **N** | L0 | Most load-bearing cost knob; invisible. |
| `subscription_tier` | FS | N | L1 | Drives VisualPolicy scene cap. |
| `tier` (deprecated, null) | FS | N | — | Mark "deprecated" so debuggers don't chase. |
| `credit_breakdown` {total, base, quality_mult, quality_credits, image/clip/multiformat_credits, formats} | FS | N | L2 | |
| `credits_charged` / `cost_estimate_cents` / `total_budget` | FS | Y | L1 | |
| `budget_by_stage` (all 0.0) | FS | Y(misleading) | — | **Never repopulated** — do NOT present as spend; use `costs{}`. |
| `content_expires_at` | FS | N | L2 | Tier-derived retention. |
| `personalization.listener_tier` | FS. | N | L1 | (=A4). |

**Content routing side-channels**
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `content_purpose/category/domain` | FS. | N | L1 | (owned A3/B; routing label). |
| `content_spec` | FS. | N | L2 | (=A5). |
| `format` (side-channel; 'short_video') | FS. | N | L1 | **Decides 9:16 vs 16:9.** ABSENT here → show ABSENT, not blank. |
| `wants_visuals / visuals_opt_out / visual_options / formats` | FS. | N | L2 | ABSENT here (stamped only when truthy). Explains why job did/didn't get video. |

**FORMAT RESOLUTION — top-level `audio_config` (the invisible core; API reconstructs from empty `audio_config_decision_log` → tiles show '-')**
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `audio_config.audio_format` | FS. | **N** | L0 | Badge (narration). |
| `audio_config.detection_reason` | FS. | **N** | L2 | **The single most valuable routing string** — verbatim "why" (educational/diagram-heavy…coerced to single-voice…superseded Style='Explainer'). |
| `audio_config.detection_confidence` | FS. | N | L1 | 0.95. |
| `audio_config.content_type` | FS. | N | L1 | educational. |
| `audio_config.speaker_count / speaker_names` | FS. | N | L1 | 1 / ['Narrator']. |
| `audio_config.provider_selection` {provider, model, tier_used, is_native, quality_score, capabilities{available_expressions, expression_guidance}} | FS. | N | L3 | |
| `audio_config.{provider_preference, force_provider, use_native_voices, high_expression_mode, stability, expressiveness, emotions, voice_style, speaking_rate, emotional_arc}` | FS. | N | L2 | |
| `audio_config_decision_log` | FS | N(absent) | — | API expects this; ABSENT → fall back to top-level `audio_config`. |
| `stage_input_logs` / `log_stage_input(initiate)` | FS/SUB | N | L3 | Verify subcollection vs doc field. |

---

## NODE GROUP D — JOB-RESEARCH-PLANNER (render the DECISION, not an empty task list)
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `stages.job-research-planner.status / started_at / completed_at / heartbeat_at / heartbeat_phase` | FS[stages] | Y | L1 | ~23.7s. |
| `stages.job-research-planner.route` {research_mode, route_reason} | FS[stages] | **N** | L1 | **API drops it** (only reads `.result`). Show **DIVERGENCE badge** when route≠actual strategy (here route='web' but ran 'none'). |
| `stages.job-research-planner.result` {research_skipped, reason, stages_skipped[], created_at} | FS[stages] | Y | L2 | reason='evergreen_topic'. |
| `research_plan` {tasks[], user_facing_summary, total_estimated_credits/duration, generated_at, model_used} | FS | Y | L3 | Web path only; null here. |
| `research_plan.tasks[]` {task_id, task_type, query, url, depth, search_options{topic,time_range,search_depth,max_results,source_class}, estimated_credits/duration, priority, rationale, enabled} | FS | P | L3 | Per-task SearchOptions drill-down. |
| `research_plan_status` (pending_approval/approved/editing/cancelled) | FS | Y | L1 | FEAT #42 state machine. |
| `research_plan_approved_at` / `research_plan_edit_count` | FS | N | L2 | |
| `awaiting_user_action` | FS | N | L1 | Guided-gate park-state. |
| `milestones[]` (research_planner: "Format locked…", "Evergreen topic — skipping…") | FS | **N** | L2 | **Read durable array, NOT `progress.timeline`** (evicts). API surfaces none. |
| `progress.timeline[]` | FS | Y | — | Ephemeral (~50 window); already evicted these ticks. Proof to use milestones. |

---

## NODE GROUP E — JOB-EXECUTE-TOOLS + JOB-RESEARCH-ASSIMILATOR (render explained-skip, not dead nodes)
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `stages.job-execute-tools.status` | FS[stages] | Y | L0 | skipped. |
| `stages.job-execute-tools.skip_reason` | FS[stages] | **N** | L1 | **API drops sibling keys.** skip_research \| llm_research. |
| `stages.job-research-assimilator.status` | FS[stages] | Y | L0 | |
| `stages.job-research-assimilator.skip_reason` | FS[stages] | N | L1 | |
| `stages.job-research-assimilator.result` {skipped, short_form} \| {skipped, skip_reason} | FS[stages] | Y | L2 | Distinct from strategy skip. |
| `research_results[]` {tool, query, data{summary, facts[], direct_answer, sources[{url,title,snippet,published_date,relevance_score}], freshness_floor, raw_results_count}, status, cost, provider, is_fallback, fallback_reason, confidence_score, cache_hit} | FS | Y | L3 | Absent here. Primary architect/script feed. |
| `research_synthesis` {summary, themes[{title, key_points}], surprising_facts[], best_sources[{name,url}]} | FS | P(summary+themes) | L2 | |
| `claim_graph` {SourceRef[{ref_id,url,title,provider,published_at,relevance_score}], ClaimNode[{claim_id,claim,claim_type,source_ref,url,confidence,supporting_quote,verification}], SubQuestion[{sub_question_id,question,recency_need,retrieval_route}]} | FS | **N** | L3 | **Richest provenance** — trace any spoken number → claim → source. Never surfaced. |
| assimilator stats: `deduplication_count, stale_content_count, freshness_warnings[], sources_used_count, output_token_count, llm_model, llm_duration_ms` | FS/stage | P | L2 | Only "N themes, M dups" reaches UI. |
| `assimilator_debug` | FS | P | L3 | Passthrough blob. |
| `tool_call_logs` | FS | P | L2 | Per-tool-call log (absent here). |
| `episode_profile.research_angles` (what was forgone) | FS. | N | L2 | (owned B). |
| coverage/gap-fill (point-coverage map, gap_fill_status) | DERIVED | N | — | **Computed in coverage.py, never persisted** — gap. |
| LLM: research task-gen, assimilator synthesis, claim_graph compiler | LLM+GCS | 0 here | L3 | |

---

## NODE GROUP F — ARCHITECT / BLUEPRINT (top anchor; render by family via presence)
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `preferences._nonfiction_blueprint` {central_question, worldview_shift{before,after}, aha_beat{setup,reveal}, succes_plan{story_spine,concrete[],emotional,unexpected,credible[]}, segment_plan[6×{role,must_cover[],must_avoid[]}], takeaway, topic, content_category} | FS[nested] | **N (not even raw dump)** | L3 | **The core contract the script is written against & judged on.** API forwards only `preferences.{audio_format,content_type,tier}`. segment_plan = the outline the audio↔visual sync map is built from. |
| `preferences._craft_decisions[]` {decision_type, chosen_value, builder, reason} | FS[nested] | N | L2 | Resolved-knob audit trail (format/genre/truth_contract/arc/persona). |
| LLM: `architect:{pedagogy_specialist, audio_craft_specialist}` (parallel) → `architect:nonfiction_director` (synthesis) | LLM+GCS | N | L3 | The calls that AUTHOR the blueprint. **cost=None, duration_ms=None** on these — gap. GCS: nonfiction_director raw response `…/llm_response/20260711_214739_*.json`. |
| **Fiction family (null here — render when present):** | | | | |
| `story_brief` + `preferences._story_brief` | FS/FS[nested] | N | L2 | |
| `arc_template` + `arc_shape` + `preferences._arc_template/_arc_shape` | FS | N | L2 | Sparkline of arc_shape floats. |
| `target_emotion_arc` + `preferences._target_emotion_arc` | FS | N | L2 | Valence/arousal 10-bucket curve. |
| `truth_contract / story_mode / story_plan` | FS | P(truth via DecisionsStrip) | L1 | |
| `preferences._story_blueprint` + `stages.architect` {candidates[N], tournament, director, helpers} | FS | N | L3 | Best-of-N pairwise tournament. |
| **Cross-link verifiers (draw edges):** | | | | |
| `stages.pre_tts_plan_gate` {blueprint_present, n_beats, n_must_cover_beats, has_named_cast, cast_size, mode, target_duration_min} | FS[stages] | N | L1 | "Contract reached the script stage." |
| `stages.wow_critique` {overall_wow, weakest_element, angle_delivered{score,critique}, emotional_truth_landed{score,critique}} | FS[stages] | Y(scores only) | L2 | **NOT linked to the blueprint it grades.** Here angle_delivered=20/100 — the promise→payoff verdict. |
| `costs.script` {model_id, total_cost_usd, output_tokens} | FS | N | L2 | |

---

## NODE GROUP G — SCRIPT GATES 1 (four first-class nodes; read `stages.*` directly)
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `stages.content_craft` {skipped, enabled, source, content_type, passed, should_regenerate, **hard_fail**, score, reasons[], regen_hints[], latency_ms, cost_estimate_usd} | FS[stages] | **N** | L2 | Deterministic $0 second judge. hard_fail can HOLD release. |
| `…content_craft.metrics` {repetition_clustered/substantive_turns, repetition_ratio, ai_tell_score, ai_tell_high_severity, host2_filler_{flagged,total,ratio}, **topic_relevance_score**(←outline_relevance.overall_score), analogy_present, has_genuine_turn, host_label_hits[]} | FS[stages] | N | L2 | Show each value vs its SOFT/HARD threshold. |
| `stages.outline_relevance` {axes.topic_relevance{status,score,reason}, overall_score, model(Haiku), brief_preview, should_regenerate, passed, regen_hints, cost_estimate_usd, latency_ms, skipped, skip_reason, error, enabled, content_type, source} | FS[stages] | **P (FE render BROKEN)** | L2 | Only gate API forwards, but FE reads `.score`/`.reason` while real object has `.overall_score` + `.axes.topic_relevance.reason` → renders null. LLM call **not** in llm_call_logs → `brief_preview` + `axes.reason` are the ONLY judge evidence; show prominently. |
| `stages.narrative_engagement` {hook_flagged/index/reason, button_flagged/index/reason, **reveal_missing**, checked, enabled, skipped, skip_reason, content_type, audio_format, regen_attempted, regen_cap, regenerated_count, regen_log, mode, tightening{double_close{cut,cut_index,kept_button_index,reason,cut_text,kept_button_text}, hedge_trimmed_count, hedge_trim_samples[], changed, enabled}} | FS[stages] | N | L3 | reveal_missing=true here despite pass. |
| `stages.name_covenant` {skipped, skip_reason, score, passed, canonical_count, found_count, missing[], invented[], notes[]} | FS[stages] | N | L2 | Distinguish "no blueprint" vs "no canonical cast" vs "low score". |

**Cross-links:** `judge_consensus`{craft_second_judge_passed, craft_second_judge_rationale, reasons[]} · `quality_gate`{outcome, release_final_passed} · `script_attempt_invalidated_count`(=2)/`_at`.

---

## NODE GROUP H — SCRIPT GATES 2 (four nodes; some skip per job — narration short: recap+crispness live, host2+pacing skipped)
| Field | Source | Now | Drill | Notes |
|---|---|---|---|---|
| `stages.host2_value_check` {skipped, skip_reason, checked, enabled, content_type, audio_format, total_host2_turns, flagged_count, flagged_indices[], flagged_samples[{index,text,reason}], regen_attempted, **mode**, regen_cap, regenerated_count, regen_log[{index,status,old_text,new_text}]} | FS[stages] | **N** | L3 | Skipped here ("no Host2 turns"). |
| `stages.recap_loop_check` {checked, skipped, skip_reason, overlap_threshold(0.6), total_candidate_turns(29), flagged_count(0), flagged_indices[], flagged_samples[{index,text,reason,overlap,closing}], regen_attempted, mode, regen_cap, regenerated_count, regen_log[]} | FS[stages] | N | L3 | Ran; closing=bookend recap flag. |
| `stages.pacing_hygiene` {skipped, skip_reason, max_turn_words(120)/seconds(30)/estimate_wpm(130), total_turns, longest_turn_words/seconds, over_cap_count, flagged_samples[{index,speaker,words,est_seconds,flag_reason,text}], energy_arc{opening_words,closing_words,longest,mean,opening_is_hook}, split_log[{index,status,flag_reason,original_words/seconds,new_turn_count/max_words/max_seconds}], split_cap, split_count} | FS[stages] | N | L3 | Skipped ("single-narrator format"). |
| `stages.crispness_check` {checked, skipped, skip_reason, total_turns(32), total_words(964), content_points(24), words_per_content_point(40.2), filler_line_count/density, backchannel_chains[]/chain_count/drop_count, restatement_count/indices[], flabby_count(7)/flabby_indices[], flabby_samples[{index,words,filler_markers[],restatement,text}], collapse{changed,skip_reason,…}, regen_attempted, regen_cap(2), tightened_count(2), **tighten_log[{index,status,old_words,new_words,old_text,new_text}]**} | FS[stages] | N | L3 | **tighten_log = the actual script edits that shipped** (seg27 49→43w, seg9 34→22w). |
| `stages.<gate>.mode` discriminator | FS[stages] | N | L0badge | detect_only_streaming vs regen — so a detect-only 0 isn't read as "clean after fix." 3 writers, last-write-wins. |
| flags SSOT (ENABLE_* + thresholds: HOST2_VALUE_MAX_REGENS=3; RECAP overlap 0.6 / max 3 / closing_lookback; PACING 120w/30s/130wpm/3 splits; CRISPNESS max 2) | code | N | L2 | Show active threshold beside each measured value. |

**Cross-link:** `content_craft.metrics.host2_filler_*` re-runs host2 detection and escalates it to a whole-script SOFT regen (host2 → release/regen edge).

---

## §9. CROSS-CUTTING / DOWNSTREAM (referenced, NOT fully inventoried — coverage gap for the tool)
Nodes named by upstream slices but with no inventory here — the DAG will still need them: **job-script** (`stages.job-script.llm_response`, `segments_ready[]`, `master_segment_timeline[]`), **job-audio**, **wow_critique** (full), **judge_consensus/quality_gate** (release), **visuals/VisualPolicy** (the bulk of the 317 `llm_call_logs` are `visuals:*`/`architect:*`/`script`/`social_clips`), **music_director**, **sfx_director**, **social_clips** (multi-platform fan-out). Flag: an "exhaustive" manifest needs their inventories before build.

## §10. EDGES THE DAG MUST DRAW (the actual value)
1. `inputs.smart_create_context`(idea) → `smart_create_outline` → `smart_create_context`(brief) → `creative_intent` → `angle_brief` → `scenario_tailoring`
2. `inputs.style/quality` + `content_category/content_domain` → **(educational-solo coercion gate)** → `audio_config.audio_format` — annotate edge with `detection_reason`
3. `subscription_tier` + `quality_tier` → `allow_premium` → `provider_selection.tier_used`
4. `format`(short_video) → aspect 9:16 vs 16:9
5. `episode_profile.skip_research` → strategy → **grey-out** `job-execute-tools` + `job-research-assimilator` with "skipped: skip_research" bypass edge (label = milestone text)
6. `route.research_mode` vs actual strategy → **DIVERGENCE badge**
7. Blueprint `central_question/chosen_angle` → `pre_tts_plan_gate` + `wow_critique.angle_delivered`(20/100) — one visual "promised → delivered → scored" thread
8. `outline_relevance.overall_score` → `content_craft.metrics.topic_relevance_score`
9. `content_craft.passed/hard_fail` → `judge_consensus.craft_second_judge_*` → `quality_gate.outcome`
10. all G+H enforcing gates → `script_attempt_invalidated_count` (hover = SOFT=free retry / HARD=holds release)
11. `host2_value_check` → `content_craft.metrics.host2_filler_*` → SOFT regen

## §11. DE-DUP REGISTRY (owned once; referenced elsewhere)
- `episode_profile` / `episode_profile_discovery_log` → **owned B**; ref C, D, F.
- `creative_intent`/`preferences._creative_intent` → **owned A6** (physically authored in D/WAVE-0).
- `angle_brief` → **owned A7** (authored in D/WAVE-1).
- `scenario_tailoring` → **owned A8**; ref C, D.
- `smart_create_context`: raw=`inputs.smart_create_context` **owned A1**; composed=top-level **owned A4** (two different strings).
- `content_purpose/category/domain` → **owned A3/B**; ref C.
- `content_spec` → **owned A5**; ref C. `personalization.listener_tier` → **owned A4**; ref C.
- `preferences._nonfiction_blueprint` → **owned F**; ref A3.
- `research_plan`/`research_plan_status` → **owned D**; ref E.
- `stages.job-execute-tools/…-assimilator.skip_reason` → **owned E**; the skip **driver** `episode_profile.skip_research` → B.
- `milestones[]` → cross-cutting durable array; each stage emits; **always read durable, never progress.timeline**.
- `awaiting_user_action` → shared A2/D.

## §12. BIGGEST GAPS vs current debug page (ranked; root cause = API allowlist)
**Root cause:** `build_debug_info` (kitesforu-api `routes/podcasts/debug_helpers.py`) surfaces only (a) 7 fixed stages `job-initiate…job-audio`, (b) a hardcoded decision-stage allowlist {`music_director, sfx_director, architect, story_judge, wow_critique, outline_relevance, quality_gate`}, (c) a `preferences` allowlist {`audio_format, content_type, tier`}; and the generic stage loop copies only `{status, timestamps, error, result}` — dropping siblings `.route`/`.skip_reason`. Everything else is dropped or raw-dump-only.

1. **Raw idea invisible** — `inputs.smart_create_context` (top-level dotted) never rendered; page shows the nested `inputs` map + the derived wrapper only. The one thing a debugger most wants is nowhere.
2. **Format decision invisible** — top-level `audio_config` (audio_format, `detection_reason`, confidence, speaker_count, provider_selection) never read; API rebuilds from the ABSENT `audio_config_decision_log` → DecisionsStrip Format/Content tiles show '-'. "Why did my short-video request become a solo narration?" unanswerable.
3. **Core blueprint invisible** — `preferences._nonfiction_blueprint` not in passthrough → 100% invisible, not even in raw dump. The most important output of the most important stage.
4. **All 8 script gates invisible** — content_craft, narrative_engagement, name_covenant, host2, recap, pacing, crispness dropped by the stage allowlist; the one forwarded (outline_relevance) renders null via FE field-name mismatch. The gates that decide release + drive the 2 regens, and the actual before/after edits (`tighten_log`/`regen_log`/`split_log`), all unseen.
5. **No provenance / no causal edges** — flat stage-card list; none of §10's edges drawn (idea→format, blueprint→wow verdict, gate→regen). Defeats the DAG's purpose.
6. **Route + skip decisions dropped** — `stages.*.route` and `stages.*.skip_reason` are siblings of `.result`; API reads only `.result`. Dynamic-research route and every skip reason invisible; route-vs-actual divergence unshowable.
7. **Tier/cost routing invisible** — quality_tier, subscription_tier, credit_breakdown, content_purpose/category/domain, scenario_tailoring, personalization, `format` side-channel, wants_visuals all omitted from the API top-level dict.
8. **claim_graph invisible** — richest "trace a spoken number to its source" artifact never surfaced.
9. **Angle-brief + authoring chain invisible** — 4-lens deliberation, angle evolution (tournament→brain-trust→skeptical-friend regen) and their prompts/GCS artifacts unreachable; `creative_intent` shown but disconnected from what produced it.
10. **Milestones not surfaced** — API never reads top-level `milestones[]`; `progress.timeline` evicts → durable "Format locked"/"Evergreen skip" lost.
11. **Dynamic-workflow blindness** — nothing keys off `content_purpose`; course/class/writeup/short jobs render identically or not at all; `content_spec.platforms` fan-out and the clarifier/`awaiting_user_action` branch unrepresented.
12. **Misleading `budget_by_stage`** — all-zero shown as if real spend.
13. **LLM-call provenance holes** — discovery (gpt-4o-mini) + base creative_intent authoring calls not in `llm_call_logs`; outline_relevance Haiku call not logged; architect/angle calls record `cost=None, duration_ms=None`.

## §13. IMPLEMENTATION NOTE
The DAG must **read the job doc directly (read-only, out-of-band — Tenet 9, zero runtime/cost/quality impact)**, keyed off the doc's actual `stages{}` keys + `stage_order` and field **presence** (never a fixed schema), OR the API passthrough must be extended to include: full top-level `audio_config`, `quality_tier`, `subscription_tier`, `content_purpose/category/domain`, `scenario_tailoring`, `personalization`, `credit_breakdown`, `content_spec`, `milestones`, full `stages.*` (route + skip_reason + all gate dicts), `preferences.{_nonfiction_blueprint,_creative_intent,_craft_decisions,_story_*,_arc_*}`, `angle_brief`, `claim_graph`, `research_results/synthesis`. Non-GCS data renders from Firestore; inline blobs may be offloaded by `common/doc_size_guard.py` only above ~1 MiB. Every value must be reachable to full depth via progressive click-in, degrading gracefully to "ABSENT" (fiction spine, clarifier, content_spec, skipped gates) so it works across podcast/short/course/class/writeup jobs.

<!-- ===== spec ===== -->

I have everything I need: the real 913-line `trace_job.py` on `origin/feature/trace-dag-bandrollup` (functions `build_trace` 144–328, `render_html` 329–892, `main` 893; the `budget.get(name)`-first cost bug at ~249; the `PHASES` topology), the working inference prototype `scratchpad/infer.py`, the cached `job_0994ebdc.json`, and all seven design dimensions + the data manifest. Here is the build spec.

---

# BUILD SPEC — Self-Contained Interactive Pipeline-Trace DAG Debug Tool

**Deliverable:** one read-only Python generator (`kitesforu-qa/scripts/trace_job.py`, extending the 913-line version on `origin/feature/trace-dag-bandrollup`) that reads a job doc + its GCS debug-artifacts and emits ONE self-contained, CSP-safe HTML file. The HTML renders any KitesForU job (podcast/short/course/class/writeup) as a top-to-bottom connected DAG with infinite drill (hover → click → deeper → ESC-back) and a recursive JSON viewer so 100% of the job data is reachable.

**Reference job (all field names below are grounded on it):** `podcast_jobs/0994ebdc-7c01-4a68-9f56-7c500bed2df2` — 24 `stages`, 317 `llm_call_logs` (302 are `visuals:*`), 27 `tts_segment_logs`, 5 `milestones`, 2 script regens. Cached at `scratchpad/job_0994ebdc.json`. Prototype inference at `scratchpad/infer.py` (67 nodes / 38 edges on this job).

**Non-negotiable invariants (Tenet 9):** the tool only READS Firestore + GCS + renders HTML. Zero provider calls, zero pipeline mutation, zero user-path latency. No external host at runtime (no CDN/font/fetch except a user-clicked `gs://` link). One HTML file, works `file://`.

---

## 0. Architecture split (who computes what)

```
GENERATOR (Python, out-of-band, $0)          BROWSER (vanilla JS, in the HTML)
─────────────────────────────────           ──────────────────────────────────
read job doc (FS)  ────────────────┐
snapshot referenced gs:// artifacts │  embed  DECODE gzip payload once
  (dedup by path, intern bodies)    ├──────►  INFER graph from raw doc  (§2)
gzip+base64 the {doc, artifacts}    │         LAYOUT (layered, hand-rolled) (§4)
emit ONE .html (template + payload)─┘         RENDER svg edges + html cards (§6)
                                              DRILL stack + tree viewer (§5)
```

Rationale (resolves the design tension): inference + layout run **client-side** so ONE template renders ANY job by swapping the embedded JSON blob (per dag-inference + dynamic-modeling). Node count stays tiny because sub-graphs are collapsed by default (~24–40 visible nodes → longest-path + barycenter is sub-millisecond, per scale-perf). The generator's only heavy jobs are GCS snapshotting, string-interning, and compression. **Escape hatch for pathological docs (>3k llm logs):** the generator can precompute `{nodes,edges}` in Python using the identical algorithm and embed the reduced graph — the renderer consumes the same contract either way.

---

## 1. Generic dynamic-workflow DATA MODEL

Embedded (gzip+base64) as `{doc, artifacts}`; the browser derives `GraphModel` from `doc`.

```ts
GraphModel = {
  meta: { job_id, workflow_type:"podcast"|"short"|"course"|"class"|"writeup",
          source_collection, status, topic, content_purpose,
          created_at, completed_at,
          totals:{ cost_usd, duration_ms, llm_calls, tts_segments } },
  phases: [ { id, label, index, lane } ],        // the fixed vertical spine (ordered)
  nodes:  { [id]: GraphNode },                    // map for O(1) drill lookup
  edges:  [ GraphEdge ],
  roots:  [ id ]                                  // spine entry points
}

GraphNode = {
  id,                       // stable, path-derived — never positional (see §1.1)
  kind:"phase"|"stage"|"gate"|"check"|"llm_call"|"tts_segment"|"artifact"|"field"|"leaf"|"group",
  label, phase, lane, order,
  status:"completed"|"failed"|"running"|"skipped"|"info"|null,
  t_start, t_end,           // ms epoch | null   (derivation §2.1)
  cost_usd, model, provider,
  badges:[ { label, value, tone:"pass"|"fail"|"warn"|"info" } ],
  summary,                  // one-line, hover essence (derived, never field-named — §6.4)
  data,                     // RAW sub-document, ARBITRARY shape (lossless — the tree viewer's input)
  childIds:[ id ],          // containment tree (drill-down)
  parentId,
  drill:{ gcs_path?, inlined?, lazy? },  // for llm_call/artifact leaves
  layout:{ x, y, w, h }     // filled by §4, not by the generator
}

GraphEdge = { from, to,
  kind:"flow"|"contains"|"fork"|"join"|"regen"|"gate_fail"|"triggers"|"ref",
  label?, dashed?, payload? }   // payload → edge stroke width (§6.5)
```

### 1.1 Stable ID convention (path-derived, the backbone of drill + deep-links)
Slash-delimited; `#` for intra-JSON pointer:
- `root` · `phase:script` · `stage:job-audio` · `gate:content_craft`
- `stage:job-audio/field:phase_timing/mastering_ms`
- `stage:visuals/group:diagram_author` · `.../group:diagram_author/call:20260711_214721_469a8d82`
- `.../call:…/artifact:user_prompt` · `.../artifact:response#/content/audio_cues/1/sfx`
- `stage:job-audio/tts:12` · `timeline/ms:seq`
- Orphans get a synthetic parent: `stage:__architect__/group:audio_craft_specialist/…`. **Never drop a node** — an un-mapped key lands in a synthetic `phase:other` bucket so ALL data stays viewable (satisfies manifest §13 "degrade to ABSENT, never blank").

### 1.2 Why `data:any` and derived `summary`/`badges` (not named fields)
The 24 `stages` sub-docs have disjoint schemas (`crispness_check.flabby_indices` vs `mastering.target_I_lufs` vs `wow_critique.hook_strength`). Any schema that names fields loses on the next workflow type. Node face info is **derived by key-pattern** (§6.4), never by field name — this is what makes course/writeup/short render with zero code change.

---

## 2. DAG-INFERENCE algorithm (pure `infer(doc) → GraphModel`)

Two layers over one interval primitive (ports the working `scratchpad/infer.py`). Layer 1 = **containment tree** (drill hierarchy). Layer 2 = **flow DAG** (vertical edges + parallel lanes). Both fall out of giving every node a `[t_start,t_end]` and computing happens-before.

Implemented as an ordered list of **pure extractors** `(doc) → {nodes[],edges[]}`, results merged. Adding a data source = add an extractor. This is the dynamic-workflow seam.

### 2.0 Extractor pipeline
1. **meta** — `workflow_type` from source collection prefix (`job-*`→podcast, `course-*`→course, `writeup-*`→writeup); totals from §2.3.
2. **phaseSpine** — emit the fixed ordered phase nodes + phase→phase `flow` edges. Phase list (workflow-agnostic; disjoint stage-name prefixes let ONE list serve all): `intake · research · plan(architect) · script · quality · audio · mastering · music_sfx · visuals · judge · output · other`. Audio + visuals share a parallel band (`fork`/`join`).
3. **stages** (workhorse, generic) — one node per key in `doc.stages` (24 here), NO hardcoded allowlist. `kind` = classify by name regex: `/_check$|_gate$|_hygiene$|_covenant$|_relevance$|_value_check$/`→`gate`; `/^(job|course|writeup|class|car)-/`→`stage`; else `stage`. `phase` = `PHASE_CLASSIFIER(name)` (substring table, manifest-grounded). `data` = raw sub-doc. **Read siblings the current API drops:** `.route`, `.skip_reason`, `.mode` (manifest §12 gaps #6, H).
4. **nestedField** — dict-of-dicts stage sub-docs (e.g. `stage_input_logs.fiction_shaping`, `phase_timing`) recurse into `kind:"field"` children with `contains` edges.
5. **llmCalls** — group `llm_call_logs` by `stage`, split on BOTH `:` and `.` (`visuals:math_classifier` → owner `visuals`; `fiction_shaping.scene_atmosphere` → owner `fiction_shaping`). Each group → `kind:"group"` node under its owner stage (or a synthesized stage if owner absent, e.g. `unknown`→nearest phase). Each call → `kind:"llm_call"` leaf. Its `*_gcs_path` → `kind:"artifact"` child with `drill:{gcs_path, lazy}`.
6. **ttsSegments** — `tts_segment_logs` → children of the audio node; cross-link `segment_beat_map` by index via `ref` edges.
7. **costs** — `costs[op].total_cost_usd` attaches `cost_usd` to the node whose id matches `op`; unmatched → a `costs` leaf in its phase. **Never read `budget_by_stage`** (all-zero, manifest §12 #12; this is the exact bug at existing `trace_job.py:249`).
8. **milestones** — `milestones[]` (durable) + `progress.timeline[]` (ephemeral) anchor phase order and add narrative labels. **Always prefer `milestones` for durable order** (manifest §12 #10). Never the sole order source.
9. **residual** (LOSSLESS GUARANTEE) — every top-level `doc` key NOT consumed above → a `kind:"leaf"` node under `phase:other`. This is why "ALL data viewable" is true by construction.

### 2.1 Timestamp aggregator `toMs` + `scanTimes` (the interval primitive)
```
toMs(v): datetime→ms; number>1e12→as-is; >1e9→*1000 (epoch-s); ISO string→parse.
scanTimes(obj, depth≤2): recurse dicts/lists, collect toMs() of any key matching
  /(_at$|^ts$|^timestamp$|_at_ms$|ran_at|scored_at|released_at|script_completed_at|audio_completed_at)/
node interval = [min, max] of every timestamp attributable to it.
```
Only 3/24 stages carry `started_at`+`completed_at`; the QA gates carry timing under varied keys (`released_at`, `ran_at`, `scored_at`, `latency_ms`). `scanTimes` recovered end-times for ALL untimed gates in the prototype (they cluster at end-of-audio release). **Duration-only phases** (`phase_timing.*_ms`) get `t_start/t_end=null` and are anchored in 2.2, marked `timeInferred:true` (dim in UI — the y-position is estimated, not measured).

### 2.2 Anchor + nest
- Build `stage→[ts]` index from milestones+timeline; fuzzy-match phase name → set its interval from those ts.
- Still-unanchored duration-only phase: place proportionally inside parent `[t0,t1]` using anchored siblings + cumulative `_ms` as fenceposts; set `timeInferred:true`.
- **Containment nesting:** level-1 node A fully inside level-0 node B (`t0_B ≤ t0_A ∧ t1_A ≤ t1_B`, 200 ms slop) and not already namespaced → `parentId=B`. Honestly surface real overlaps (job-script window overlaps job-audio in streaming arch — don't force a false chain).
- **Unify:** `canonicalId` strips `phase:`/`llm:` prefixes + applies an alias table (`music_director↔music_supervisor↔music_render`, `sfx_director`, `mastering`, `casting`, `architect`, `audio↔audio_segment`). Merge dups: union intervals, sum count/cost, concat sources (fixes the observed `phase:mastering` vs sub-stage `mastering` duplication).

### 2.3 Flow edges = transitive reduction of interval happens-before (per parent group)
```
timed = siblings with both t0,t1, sorted by t0
for each B:
  preds = { A : t1_A ≤ t0_B + 200ms , A≠B };  if none → B is a group root (edge from parent)
  frontier = { A∈preds : t1_A ≥ max(t1 over preds) − 1500ms };  emit A→B for each frontier A
```
Yields sequential chains, fan-out (1→N overlapping), fan-in (N→1); overlapping siblings stay edge-less = **parallel**. Nodes with no interval (truly skipped, e.g. `job-execute-tools`) get a dashed edge from nearest namespaced/temporal ancestor, rendered greyed off the critical path. Regen: `script_attempt_invalidated_count=2` or any `should_regenerate:true`/`regen_*` key → dashed `regen` back-edge to the authoring node.

### 2.4 Semantic edge overlay (the DAG's actual value — manifest §10)
On top of temporal edges, draw these **named** causal edges when both endpoints exist (override temporal for the pair):
- idea→outline→brief→creative_intent→angle_brief→scenario_tailoring
- (style+content_category+content_domain) → **educational-solo coercion** → `audio_config.audio_format`; edge label = `audio_config.detection_reason` (the single most valuable routing string)
- `route.research_mode` vs actual strategy → **DIVERGENCE badge** (route='web' but ran 'none')
- blueprint `central_question/chosen_angle` → `pre_tts_plan_gate` → `wow_critique.angle_delivered` (promised→delivered→scored, one thread)
- `outline_relevance.overall_score` → `content_craft.metrics.topic_relevance_score`
- `content_craft.passed/hard_fail` → `judge_consensus.craft_second_judge_*` → `quality_gate.outcome`
- all enforcing gates → `script_attempt_invalidated_count` (hover: SOFT=free retry / HARD=holds release)
- `host2_value_check` → `content_craft.metrics.host2_filler_*` → SOFT regen
- `claim_graph` provenance: spoken number → ClaimNode → SourceRef (trace-a-fact edges)

### 2.5 Concurrency lanes (for §4 side-by-side layout)
Among a parent's children, greedily interval-partition overlapping intervals into lanes; `laneIndex → x-offset`, `t0 → y`. Where timestamps are absent, fall back to the small declared fork-set `{audio, visuals}` (one 2-entry constant — the ONLY pipeline-specific coupling, isolated). Critical path = longest-duration path through flow edges (highlight).

---

## 3. Graph library + self-contained inlining

**Decision: NO runtime graph library (TIER 1).** Layered layout is hand-rolled in ~200 lines of vanilla JS (§4). Rationale (unanimous across graph-lib/layout-algo/dynamic-modeling): the graph is a DAG-by-construction near-linear spine (`get_next_stage` returns one successor; only real fork is script→{audio‖visuals}), so Sugiyama's hard phases (cycle removal, NP-hard crossing-min) are no-ops. Bundle-collapse + interval-lanes are things dagre/ELK do NOT do natively. Node count peaks ~400 (podcast), collapsed to ~30 visible — far under any canvas-lib threshold. Inlining dagre (~50 KB gz) or ELK (~1.4 MB) buys nothing here.

**Substrate:** SVG cubic-Bézier edge layer (z below) + absolutely-positioned HTML `<div>` card nodes (z above), inside a `transform: translate() scale()` pan/zoom viewport. DOM cards (not canvas) give pixel-perfect themeable cards, native hover/keydown, a11y, and the bespoke ESC-back drill stack. Runtime JS ≈ a few hundred lines, no deps, no fonts, no network.

**Documented escape hatch (TIER 2, only if real jobs produce ugly layouts):** vendor `dagre.min.js` to `kitesforu-qa/assets/vendor/dagre-<version>.min.js`, pin the exact version + record its SHA-256 in a sidecar, the generator reads the file at emit time and inlines it verbatim inside `<script>…</script>` (never `<script src=CDN>`). One-time fetch at vendoring time, asserted by checked-in SHA → hermetic, offline-reproducible, zero runtime requests. Same `{nodes,edges}` contract, so it's a drop-in for Passes A/D only. Do NOT inline d3 or dagre-d3.

---

## 4. Top-to-bottom LAYOUT — hand-rolled `layout(nodes, edges, opts)`

`opts = { rowGap:120, laneGap:220, nodeW:180, nodeH:64 }`. Six passes, O(V+E), re-runnable recursively at every drill level (top spine, an expanded macro's sub-graph, a bundle grid).

- **A. LAYER (rank):** virtual ROOT → all sources; `layer[v]` = longest path from ROOT via Kahn topo + relax. Break `regen` back-edges first to keep acyclic. Fan-out children inherit `parent.layer+1`. `y = layer * (nodeH + rowGap)`.
- **B. LANE:** per layer, two nodes are CONCURRENT iff intervals overlap OR (timestamps absent) both ∈ fork-set. Concurrent nodes get lane index k; `laneX(k) = centerX + (k − (L−1)/2)*laneGap`. Non-concurrent → lane 0 (single vertical spine). Same rule lanes concurrent course lessons with zero pipeline knowledge.
- **C. BUNDLE-PACK:** a `group` with ≥8 sibling leaves (302 `visuals:*`, 27 tts) renders COLLAPSED as ONE node "`N × <classifier>`". On expand, its N leaves lay out as a **wrapped grid** (`cols = clamp(round(√N),1,6)`) inside a bounded cluster-card sub-canvas — NOT graph layers (24 leaves → 5×5 card, not a 24-wide row). Keeps the spine to ~6–8 visible nodes.
- **D. ORDER:** layers with >1 edged node → 2 barycenter/median sweeps (order by mean x of adjacent-layer neighbors). Skip on ≤1-wide layers.
- **E. COORDS + STRAIGHTEN:** `x = laneX(node) + withinLaneSpread`; then a Brandes-Köpf-lite median-align pass pulls each single-parent/single-child node toward its neighbor's x to straighten the spine and kill bends.
- **F. EDGES:** cubic Bézier top→bottom `M(px, pcy_bottom) C (px, mid)(cx, mid)(cx, ccy_top)`. Fork edges diverge to lane x's; converging lanes mirror. Since layers are discrete rows, edges span one row-gap → minimal overlap, no obstacle routing.

Worst-case laid-out-at-once V < ~30 (bundles stay collapsed until drilled) → re-layout on expand is imperceptible. Memoize each sub-graph's layout by nodeId. Expand can be either **in-place accordion** (reflow spine, push lower layers down by sub-graph height) or **in-drawer** — both are renderers of one `layout()` result; ship in-drawer for v1, in-place for stacked nodes as a follow-up.

---

## 5. Interaction / navigation-stack model + recursive tree viewer

**Dual-axis** (per interaction-drill + recursive-detail-viewer): a push/pop **drill STACK** for crossing semantic boundaries (graph-node → node-detail → record → full text → parsed-JSON subtree), plus a zero-dep **inline-twisty TreeView** for cheap structural nesting. Hover is a THIRD, off-stack axis.

### 5.1 Boot (once, $0)
```js
const b64  = document.getElementById('__DATA__').textContent;
const bytes= Uint8Array.from(atob(b64), c=>c.charCodeAt(0));
const json = new DecompressionStream                       // feature-detect
  ? await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text()
  : /* fallback: embedded plain-JSON <script> */ PLAIN_JSON;
const {doc, artifacts} = JSON.parse(json);                 // ~30–50 ms once
const MODEL = infer(doc);                                  // §2
const byId  = MODEL.nodes;                                 // O(1) drill lookups
```
No runtime fetch except a user-clicked `gs://` link. Heavy artifact bodies live in `artifacts` (interned/deduped by the generator), resolved lazily on pane mount.

### 5.2 Drill stack (single active sliding pane)
```js
const drill = {
  stack: [ {kind:'graph', id:'root', title:'Job '+shortId} ],
  hover: null,                                             // OFF-stack
  push(ref){ this.stack.push(ref); history.pushState({d:this.stack.length},''); this.sync(); },
  pop(){ if(this.stack.length>1){ this.stack.pop(); this.sync(); } },
  popTo(i){ this.stack.length = i+1; this.sync(); },       // breadcrumb
  reset(id,kind){ this.stack=[ROOT,{id,kind}]; this.sync(); }, // lateral graph jump
  top(){ return this.stack.at(-1); },
  sync(){ renderCrumbs(this.stack); renderPane(this.top()); dimGraph(this.stack.length>1); }
};
addEventListener('keydown', e=>{
  if(e.key==='Escape'){ if(closeOpenSearch()) return; drill.pop(); }        // layered ESC
  if(e.key==='Backspace' && !isTyping(e)) drill.pop();
});
addEventListener('popstate', ()=> drill.pop());            // browser Back == ESC
```

### 5.3 Transition table
| From | Gesture | To | Effect |
|---|---|---|---|
| graph node | hover | (unchanged) | ephemeral tooltip (summary+badges); **NO stack change** |
| graph node | click | DRILL 1 | `reset(id)`; pane slides in from right |
| graph node (stacked) | click chevron | DRILL 1 | bloom sub-graph via `layout()` (§4-C) |
| pane row (child) | click | DRILL d+1 | push; active pane slides left+dims (peek) |
| pane row (text field) | click | DRILL d+1 | push `text` view |
| any view | "Raw ▾" | DRILL d+1 | push `tree(view.data)` — **universal hatch** |
| tree row (obj/array) | twisty | inline | expand INLINE, lazy-build kids (NO push) |
| tree row (json-string) | chip | DRILL d+1 | push `tree(parseLoose(value))` (infinite by construction) |
| tree row (gs://‖http) | chip | DRILL d+1 | push `text(artifacts[ref].content)` / open link |
| graph node (other) | clickGraphNode m | DRILL 1 | `reset(m)` (lateral jump; browser Back restores prior stack) |
| any | ESC / Back / crumb | pop / popTo | reverse slide |
| any | clickBackdrop | DRILL 0 | close, clear highlight |

Breadcrumb = `stack.map((ref,i)=> button(title(ref), ()=>drill.popTo(i)))` joined by "›" + depth badge + "ESC ↩" hint (mirrors existing `DebugBreadcrumbs.tsx`). A stacked-card shadow whose layer count == depth gives a persistent "how deep am I" cue; `aria-live=polite` announces "Entered {title}, level {d} of {n}".

### 5.4 Recursive TreeView (guarantees ALL data viewable, schema-agnostic, lazy)
Dispatch on runtime type; build children only on expand; window big arrays at 100.
```js
const STR_PREVIEW=120, ARRAY_PAGE=100;
function classify(v){
  if(v===null) return 'null';
  if(Array.isArray(v)) return 'array';
  const t=typeof v;
  if(t==='object') return 'object';
  if(t==='string'){
    if(looksJson(v))                     return 'json-string';   // the GCS ```json content case
    if(/^gs:\/\/|^https?:\/\//.test(v))  return 'url-string';
    if(isIso(v))                         return 'date-string';
    return 'string';
  }
  return t;                                                       // number | boolean
}
function looksJson(s){ const t=s.trim().replace(/^```(json)?/,'').replace(/```$/,'').trim();
  return (t[0]==='{'&&t.at(-1)==='}')||(t[0]==='['&&t.at(-1)===']'); }
function buildChildren(box, value, path){
  const entries = Array.isArray(value) ? value.map((v,i)=>[String(i),v]) : Object.entries(value);
  paginate(entries, ARRAY_PAGE, (k,v)=> box.append(makeRow(k, v, [...path,k])));  // "▾ 100 more (of N)"
}
```
Each expandable row lazily builds kids on first open (a `built` guard). Boundary-crossing leaves PUSH a deeper view (json-string → parse & drill; long string → text view; `gs://` → artifact text or "(not snapshotted)" fail-open; `parseLoose` try/catch → falls back to text on failure). A `seen` WeakSet guards cycles. Per-tree toolbar: [Search "/" ] [Copy path] [Copy value] [Collapse all]; search force-builds + expands matching paths, dims non-matches, shows "N matches". Only the TOP stack view is mounted; lower views are cheap descriptors re-rendered on pop.

Curated node-detail views are an OPTIONAL projection over the same `data` (a small `NODE_HINTS` registry: `job-audio`→TTS segments + phase_timing; `job-script`→model + LLM calls). If a hint is absent (unseen workflow stage), the always-present "Raw ▾" tree covers it. Curation never gates coverage.

### 5.5 Keyboard + history
ESC/Backspace=pop; Enter=drill focused row; ↑/↓=move focus among sibling rows; ←=pop, →=drill; `/`=fuzzy node-jump search → `reset`. On push, focus the pane `<h2>`; pane is `role=region` with Tab-trap. Each DRILL/reset `pushState`s `#p=<path>`; on load, split path and resolve each segment against `byId` to rebuild the stack (invalid tail → deepest resolvable node). Deep-links are shareable and still zero-runtime.

---

## 6. Node/edge visual encoding + failure-surfacing + aesthetic

### 6.1 Four ORTHOGONAL node channels (never reuse a channel for two meanings)
| Channel | Encodes | Rendering |
|---|---|---|
| **HUE** | lane/phase IDENTITY only | 5px left stripe + ~8% card tint |
| **STATUS CHIP** | execution × gate-verdict | filled pill, GLYPH + traffic hue, FIXED top-right corner |
| **HEIGHT** | duration (dominant magnitude) | `h = H0 + K·√(dur_share)` (660s audio dwarfs a 2ms gate) |
| **COST underline** | cost share | bottom heat bar, hue-free LUMINANCE ramp (gold→dark), width∝share |
| **#CALLS badge** | call/segment count | "302", "27 tts" + optional dot-density micro-row |
| **Silhouette** | owns-sub-graph | stacked offset shadow cards + `+` chevron (vs flat leaf card) |

Shape+glyph carry meaning so the chip survives beside the orange visuals lane. Regen action = a rotate-glyph overlay (`regen_attempted`). Every channel has an explicit ABSENT rendering distinct from ZERO (hollow gauge + "?" for unknown duration; no underline for absent cost, 1px tick for known-$0).

### 6.2 Status chip = execution × verdict (the failure-surfacing axis)
Composite of `_status(sdata,cl)` × gate fields (`passed`/`final_passed`/`hard_fail`/`should_regenerate`/`flagged_count`/`overall_wow<threshold`):
- **✓ green** done+passed · **!N amber** done+flagged(N) · **✗ red** failed | hard_fail | should_regenerate | gate_fail · **⤫ gray dashed** skipped (hover → `skip_reason`) · **◍ blue pulse** running.
This is the whole point of the tool — spotting which gate flagged or fired a redo. On the reference job: `wow_critique.overall_wow=36`, `content_craft.hard_fail`, `crispness_check.regen_attempted=true`, `name_covenant` skipped ("no _story_blueprint"), `angle_delivered=20/100`.

### 6.3 Palette (dark, orange accent — semantic kept PERPENDICULAR to accent)
- **Lanes (identity):** intake slate `#7d8590` · main blue `#58a6ff` · audio violet `#bc8cff` · **visuals orange `#f0883e`** · quality teal `#39c5cf` (moved OFF green so it can't read as "pass").
- **Semantic (chip+glyph ONLY):** ok `#3fb950` · warn `#d29922` · crit `#f85149` · skip `#6e7681`.
- **UI accent = orange** (`#f0883e` family): focus ring, active breadcrumb, selected node outline, edge-lineage highlight, buttons. Distinguished from the visuals **lane** tint by saturation + usage context (accent = full-sat interactive; lane = 8% tint + stripe).
- **Surface:** GitHub-dark base (`#0d1117` canvas, `#161b22` cards, `#30363d` borders). Theme-aware: `@media (prefers-color-scheme)` default + `:root[data-theme]` override both directions.
No hue is ever both an accent and a status. Cost is dual-coded (luminance + width) since luminance is a weak channel.

### 6.4 Derived `summary`/`badges` (no field names — `deriveHeadline(data)`)
Scan keys by priority: booleans `/passed|success|applied|enabled|skipped|_recommended$/`→pass/fail chip; numbers `/score|overall_|_pct|coverage|cost|_count|_ms|lufs/`→metric chip; strings `/skip_reason|reason|error|genre|theme_id|model|provider|voice_id/`→identity/reason chip. Top ~4 → badges; `summary` = first reason/skip_reason/error or "{n} fields". Gives `content_craft→{passed,score,hard_fail}`, `mastering→{applied,genre,target_I_lufs}` with ZERO schema knowledge. Show each measured value **beside its active threshold** (flags SSOT: `HOST2_VALUE_MAX_REGENS=3`, RECAP overlap 0.6/max 3, PACING 120w/30s/130wpm, CRISPNESS max 2) so a detect-only 0 isn't misread as "clean after fix" — surface `.mode` as an L0 badge.

### 6.5 Edge encoding (data-FLOW, not just order)
Target arrowhead (direction) · stroke width `1.2 + 0.8·log1p(payload)` from `outputs.script_size_bytes`/`len(segments_ready)=27`/`len(smart_create_outline)=5` · hover label names the artifact ("script → 27 segments", "4 music cues") · dashed for `fork`/`join`/`regen`/skipped-bypass · on node-select, `.hot` (orange) highlights the FULL upstream lineage (walk edges backward). Divergence badge on route≠actual edges (§2.4). Absent payload → uniform thin edge, hover label still names the flow.

### 6.6 Cost/duration data-source fixes (the current tool renders ~$0 everywhere)
Replace `budget.get(name)`-first (existing `trace_job.py:249`) with `costs{}` attribution:
- script/job-script ← `costs.script.total_cost_usd + costs.script_attempt_2.total_cost_usd` (~$0.215)
- visuals ← `costs.{geometry_author,diagram_author,figure_author,visuals_art_director}.total_cost_usd` (~$0.116)
- job-audio ← `costs.tts_usd_actual` ($0.982 — 69% of the real $1.42 total)
- per-gate inline ← `wow_critique.cost_usd`, `content_craft.cost_estimate_usd`, `judge_consensus.cost_estimate_usd`
- header total ← SUM of the above (~$1.42), **NOT** `costs.total_usd_estimate` (0.44, excludes TTS).
Duration derivation per node: first non-null of `[completed_at − started_at]`, `[max−min owned-call timestamps]`, `phase_timing[f"{shortname}_ms"]`. Store `dur_known:bool` → render derived values in lighter weight than measured.

---

## 7. The GENERATOR — extend `kitesforu-qa/scripts/trace_job.py` (Tenet-9)

Base = the 913-line file on `origin/feature/trace-dag-bandrollup` (`build_trace` 144–328, `render_html` 329–892, `main` 893). It already: reads `podcast_jobs` (or `--from-file`), reconstructs the phase spine with audio‖visuals fork/join, is self-contained + read-only + never writes/triggers (Tenet 9 header comment lines 13–19). Keep the CLI (`trace_job.py <job_id> [--out …]` / `--from-file` offline $0). Extend, don't rewrite.

**Refactor into the new split (the current file bakes topology + Python-side render):**
1. **Generalize `_load_doc`** to detect `source_collection` by prefix so course/short/writeup jobs load (not just `podcast_jobs`).
2. **Replace `build_trace`'s hardcoded `PHASES`/`STAGE_PHASE` structural role** — keep `PHASES` only as the optional decorative overlay (lane color/label/tie-break, per dag-inference "overlay decorates, never structures"). Move node/edge derivation to the browser (§2); the Python side now just (a) snapshots GCS, (b) interns+compresses, (c) emits the template. If you keep any Python inference for the pathological-doc path, it must be the SAME algorithm as §2.
3. **Fix cost attribution** (§6.6) — this alone fixes existing `:249`, `budget_by_stage` misuse, and the header undercount. Add a `COST_CHANGELOG.md` note? No — this tool makes **no** per-unit generation cost change (read-only), state "cost-neutral (read-only debug tool)" in the PR body.
4. **GCS snapshot (new, Tenet-9-clean):** collect every `*_gcs_path` referenced in `llm_call_logs`; dedup by path (307 refs → ~164 unique on this job); fetch the unique set via bounded `ThreadPool(32)` (out-of-band; ~30–60 s to regenerate, never on user path); intern bodies into a `strings[]` table; build `artifacts:{path→{content,metadata}}`. Bound size: embed only `*_gcs_path`-referenced artifacts (tens of KB), cap any single body >200 KB with "truncated — see gs://…". Falls open if a path 404s.
5. **Payload emit:** `json.dumps({doc, artifacts, strings}, separators=(',',':'))` → `gzip.compress(9)` → base64 → inline as `<script id="__DATA__" type="application/octet-stream">…</script>`. Also emit a plain-JSON `<script type="application/json">` fallback for pre-DecompressionStream engines. File target ≤ ~550 KB (480 KB base64 of 360 KB gz) vs 1.95 MB raw.
6. **Replace `render_html`'s Python-generated markup** with a static template + the vanilla-JS runtime (§4/§5/§6) inlined. The Python side no longer computes layout or per-node HTML — it just embeds the template + payload. Keep the existing "Costliest/Slowest" ranked lists (existing `:845–846`) as a job-data drawer.

**Tenet-9 compliance statement (put in PR body, per `debug-tooling-zero-impact.md`):** adds no user-path latency (offline generator, runs after the job completes), no new provider calls (reads the doc + already-emitted `debug-artifacts/`), no artifact mutation (read-only; the tool cannot write a job or trigger generation). All drilling is in-memory Map lookups; the only network is a user-clicked `gs://` link.

**Verify ($0, T0/T1 — no live generation):**
- Unit-test `infer(doc)` on `scratchpad/job_0994ebdc.json`: assert 24 stage nodes; gate/stage/check tagging; regen edge for `script_attempt_invalidated_count=2`; children counts (27 tts + 317 llm grouped, 302 under `visuals`); `costs`-based totals ≈ $1.42 not $0.44; every top-level doc key reachable (residual extractor).
- Render the HTML for `0994ebdc…` and open with Playwright: screenshot top-to-bottom layout; assert hover highlight, click-drill, ESC-pop, a `gs://` leaf resolves from `artifacts`, and the "Raw ▾" tree reaches `stages.job-script.composer_log.builders_fired[3]`.
- No T3/T4 job needed — the tool only READS.

---

## 8. Prioritized backlog — "v1 build then keep-adding"

Ordered so each step is demoable; higher items close the biggest manifest §12 gaps first. Ship behind no flag (it's an offline script). PR-per-slice on `feature/trace-dag-*`.

**v1 — the spine that beats the current tool (ship first):**
1. **Payload + boot:** generator snapshots GCS + gzip+base64 embed; browser decodes; `infer(doc)` ports `scratchpad/infer.py`; assert 24 nodes on the ref job. *(closes §12 #11 dynamic-workflow blindness at the data layer)*
2. **Layered layout + render substrate:** hand-rolled `layout()` (§4 A–F); SVG edges + HTML cards; pan/zoom; top-to-bottom spine with audio‖visuals fork.
3. **Node channels + status chip + cost/duration fix:** 4 orthogonal channels (§6.1–6.3), verdict chip (§6.2), `costs{}` attribution (§6.6). *(closes §12 #2 format decision, #12 misleading budget, #4 gate verdicts at a glance)*
4. **Drill stack + recursive TreeView:** hover tooltip; click→pane; "Raw ▾" tree on EVERY node; ESC/Back/breadcrumb; `gs://` artifact resolution. *(closes §12 #1 raw idea, #3 blueprint, #4 all 8 gates, #8 claim_graph — all now reachable via tree)*

**v1.1 — the causal edges (the DAG's reason to exist):**
5. **Semantic edge overlay** (§2.4): idea→…→angle chain, coercion edge w/ `detection_reason` label, route-vs-actual DIVERGENCE badge, blueprint→wow_critique thread, gate→regen edges. *(closes §12 #5 provenance/causal edges, #6 route/skip drops)*
6. **Bundle collapse/expand** (§4-C): 302 `visuals:*` → one "N × classifier" node → grid → call table → single call (prompts + response from `artifacts`) → parsed-JSON tree. *(closes §12 #13 LLM-call provenance holes)*

**v1.2 — completeness + polish:**
7. **Curated `NODE_HINTS`** for high-value kinds (`job-audio`, `job-script`, gates) — pretty projection over the same `data`; tree stays the fallback.
8. **Failure-first affordances:** "jump to first failed/flagged gate" hotkey; critical-path highlight; lineage `.hot` on select.
9. **Deep-links + history** (§5.5): `#p=<path>` shareable, browser Back == ESC.
10. **Windowed rows** for 24+-child groups; `content-visibility:auto` on tree subtrees + off-screen panes; perf self-check (file ≤550 KB, boot ≤120 ms, expand ≤100 ms, pane ≤50 ms).
11. **Cross-workflow proof:** run the generator on a course + a writeup + a short job; confirm each renders (residual extractor guarantees no dropped keys); add per-workflow `PHASE_CLASSIFIER` substrings as needed. *(closes §12 #11 fully)*

**Keep-adding (dormant until needed):** TIER-2 vendored `dagre.min.js` if a real job lays out poorly; Canvas renderer if visible nodes ever exceed 1500; in-place accordion expand for stacked nodes; re-home the same `{nodes,edges}` contract + TreeView into the Next.js `/debug` route as a React component (contract is framework-agnostic).

---

**Files:** generator `kitesforu-qa/scripts/trace_job.py` (extend the `origin/feature/trace-dag-bandrollup` version); optional vendor dir `kitesforu-qa/assets/vendor/` (TIER-2 only); tests `kitesforu-qa/tests/test_trace_infer.py`; fixtures reuse `scratchpad/job_0994ebdc.json` + `scratchpad/infer.py`. Output HTML written to `--out` (default alongside the job id), never committed.