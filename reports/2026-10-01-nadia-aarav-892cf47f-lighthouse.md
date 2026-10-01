# Hero-user review — job 892cf47f (lighthouse drama, low tier), 2026-10-01

Workflow `.claude/workflows/hero-user-verification.js`, surface `episode`, personas: nadia-story-listener, aarav-audio.
Job `892cf47f-f5e5-4fa5-9c34-759d786c9600`. Surfaced artifact: https://storage.googleapis.com/kitesforu-dev-podcasts/visuals/892cf47f-f5e5-4fa5-9c34-759d786c9600/episode_video.mp4. Overall ship: **False**.

Each critic was a separate agent given the artifact and the brief only. This file is the verbatim record of what
they said, kept so later PRs can cite it (workers #3247, #3248 cite the visual-continuity finding).

## Headline

REJECT. Job 892cf47f, "lighthouse keeper vs pilot", a 66.4s drama at quality_tier=low. Both personas scored it 1/5 and would not ship it. L0 passed deterministically (REVIEW, exit 0; the one MAJOR flag, EDGE-CLIP, is very likely a false positive from the gate's own uncovered tail), but the vision/adversary step was never run. The artifact fails on what a listener notices first. The male pilot speaks every line in a female voice (inworld/Naomi), and that voice is higher than the woman he is rescuing (210 Hz vs 177.6 Hz on clean stems). The protagonist's climactic acceptance line is a bare '...' that a different vendor's fallback voice reads as a garbled 'He stepped'. The story never resolves and ends on 11.4s of bright major-key music over a frozen still. Across 4 images the two characters change face, hair and clothes 3 times, which is the founder's standing continuity complaint, still unfixed. The pipeline's own gender check reports 'clean' and its prosody detector flagged 'metronomic', and the episode shipped anyway.

## L0 gates (deterministic, $0)

- JOB RESOLVES: PASS. Firestore podcast_jobs/892cf47f-f5e5-4fa5-9c34-759d786c9600 exists=True. status=completed, format=drama, quality_tier=low, subscription_tier=ultimate. Created 2026-10-01 11:10:56Z, completed 11:17:50Z.
- SURFACED (visual.video_url non-empty): PASS. https://storage.googleapis.com/kitesforu-dev-podcasts/visuals/892cf47f-f5e5-4fa5-9c34-759d786c9600/episode_video.mp4. visual.video_status=ready.
- FETCHABLE: PASS. Downloaded 11,562,888 bytes over HTTPS (not a bare gs:// path).
- TRUE DIMS / DURATION (ffprobe): 1920x1080 landscape, 66.43s. Streams: h264, aac (66.43s), and a mov_text subtitle stream that ends at 53.86s.
- INVARIANT A, ORIENTATION MISMATCH: PASS. The landscape video matches the clip aspects ['16:9'].
- OFF-TOPIC VEO ON EDUCATIONAL CONTENT: PASS. 0 video_hero clips; all 8 clips are scene_image.
- INVARIANT B, LETTERBOX: PASS. Not flagged on the 12 sampled frames.
- INVARIANT C, EDGE-CLIP: FLAGGED, sev MAJOR. 'EDGE-CLIP: 2/2 checked frames have bright/text pixels hugging the frame edge (10 photo frame(s) exempt)'. The 2 checked frames are f_020 (57s) and f_022 (63s). Running the gate's own _clip_modality_at shows both return modality=None, because the clip timeline ends at 55,174 ms and the video runs to 66,433 ms. Viewed by eye, both frames are the same full-bleed scene_image as clip 8 (keeper and pilot by the stair hatch), with no text or box cut off. This is very likely a false positive from the uncovered ~11s tail, not a real cut.
- GATE VERDICT: REVIEW, exit 0. No BLOCKER, so this is a deterministic pass. The vision/adversary step has NOT been run.
- NON-GATE OBSERVATIONS FOR THE ADVERSARY (my eye, not gate output): (1) Only 4 distinct images in 66s; clips are near-duplicate pairs (0-15s, 15-27s, 27-45s, 45-66s). (2) The final image holds for about 20.7s (45.7s to 66.4s), and its last ~11s comes after the last spoken line. (3) Character continuity drifts: the keeper has loose hair in frames 1-5 and a braid from frame 6 on; the pilot has short hair in frames 1-15 and long locs in frames 16-22. (4) Every frame carries a top-right badge 'AI-generated · for educational illustration' on a drama. (5) The two script lines that are only '...' (indices 6 and 18) come out of whisper as 'It's stepped' / 'You step', so the TTS may be voicing the ellipsis. Needs an ear check.

L0 verdict: **pass**

## Nadia, the late-night story listener (fiction, drama, romance). Job 892cf47f, "lighthouse keeper vs pilot", a 1-minute drama with scene_images.

Score 1/5, ship: **False**.

**Worst defect.** The story's turn is broken in both sound and picture. A woman's voice (inworld/Naomi, female per the provider's own map; measured pilot F0 of 200-250 Hz against the keeper's 167-202 Hz) comes out of the man in the orange flight suit for every pilot line. The keeper's single acceptance beat (script line 18, a bare '...') is voiced by a different provider's fallback voice (OpenAI shimmer) as a garbled 'He stepped' at 52.7s. Then the episode stops on 11s of storm noise over a frozen picture of two strangers who have changed faces three times. She never decides, never leaves, and the boat is forgotten.

**Ranked defects**

1. 1. THE PILOT IS VOICED BY A WOMAN (audio, all 10 pilot lines, 0:02-0:55). Every pilot line is rendered on inworld/Naomi, which the provider's own map registers as female (kitesforu-workers/src/workers/stages/audio/providers/inworld/shared.py:338, "Naomi": "female"; cast persona isabella-crane, _pitch_estimate_hz 195). Yet tts_segment_logs stamps him gender=MALE, and all 22 frames draw him as a man in an orange flight suit. My autocorrelation pitch estimate on the mixed master gives the pilot a median F0 of 200-250 Hz on 9 lines, against 167-202 Hz for the late-50s keeper, so the man speaks higher than the woman he is rescuing. Two independent instruments agree; one human ear should still confirm. Cause, visible in the job doc: voice_cast.contract.voice_map has only Host1/Host2/Host3/Narrator, all with requested_gender=None and fallback_flag 'tier_default_no_archetype'. The Pilot landed on Host2 (Naomi) while a male voice (Host3, Felix, 115 Hz) went unused. 'You take my hand' in a woman's voice out of a man's mouth.
2. 2. THE CLIMAX IS A BLANK, VOICED BY A STRANGER (52.68-53.52s; same fault at 24.16-25.16s). Script lines 6 and 18 for the keeper are a bare '...'. Inworld returned empty audio for them, so the line fell back to OpenAI tts-1 'shimmer', a different provider and a different voice for the same woman. That voice utters a garbled non-word: whisper hears 'It's stepped' at 24s and 'He stepped' / 'You step' at 53s. Meanwhile the caption track shows '...' on screen. Line 18 was her one beat of surrender, the whole point of the story.
3. 3. THE ENDING DOES NOT RESOLVE (f_016-f_022, 45.7-66.4s). After the garbled beat comes 'Come on, Mara.', then 11s of storm noise and a music sting over a frozen image of the two of them standing still in the lantern room. She never says yes, never moves and never leaves. The boat is never mentioned again. The metadata's arc of 'bittersweet acceptance' is not in the artifact. It stops; it does not end.
4. 4. CHARACTER CONTINUITY FAILS, the founder's standing complaint, unfixed (4 images, 4 keepers, 4 pilots). The KEEPER has loose shoulder-length hair, no braid and no clear scar (f_001-f_005, 0-12s). She then has a braid, a green knit cap, a face roughly ten years older and mustard trousers (f_006-f_009, 15-24s), then a braid with no cap and black trousers (f_010-f_015, 27-42s), then a braid and olive trousers (f_016-f_022). The PILOT is a short-cropped man (img1), then a different face (img2), then a third face that now wears a US-flag patch and name tag (img3), then a fourth man with long locs and a mustache (f_016-f_022, 45-66s). The SET drifts too: a giant brass Fresnel lens beside an electric console (img1) becomes a smaller lens on a pedestal with a floor hatch (img2), then a portable lantern on the floor next to a wooden door (img3), then no lamp at all (img4). The light the story is about is missing from the final picture.
5. 5. THE PICTURES CONTRADICT THE SPOKEN LINE AT THE TWO PHYSICAL BEATS (H1). At 0:00 she says 'The red flare is still damp.' while holding a BURNING red flare (f_001-f_005). At 45.7s the pilot says 'I'll take the key. You take my hand.' over an image where SHE holds a foot-long cartoon brass key and he grips her wrist (f_016-f_022). The only gesture of trust in the story is drawn backwards. At 28.4s 'It's already flooding' refers to the engine room below, but the image shows surf breaking around their ankles inside the glassed lantern room at the top of the tower (f_010-f_015).
6. 6. THE STORY CONTRADICTS ITSELF (script). The pilot sets the stakes, 'That beam has to guide the evacuation boat' (2.1s) and 'The boat needs that light. They're relying on us.' (34.3s), then immediately says 'Then come with me.' (39.9s). He argues the light is life or death and then asks her to abandon it. Nobody fixes the generator, and the boat is dropped. Then comes 'My father... he stayed.' / 'He did. And we're going to honor that.' (47.9-52.7s): honoring a man who stayed at his post and drowned by LEAVING the post is backwards, and the pilot confirms a family history he cannot know. Nobody has ever said that sentence out loud. The blueprint's ghost (the father drowned when a harbor light failed) is crushed into four words. The one character-revealing beat ('I trim the wick' / 'it's electric now', 16-24s) is set up and never paid off.
7. 7. RAIN FALLS INDOORS IN EVERY FRAME (f_001-f_022). Rain streaks cross the figures standing inside the enclosed lantern room. In img3 the sea is inside the top of the tower; in img4 waves sit at window height on a lighthouse gallery. She notices the machine in every picture.
8. 8. EXPLAINER REGISTER IS PRINTED ON THE FICTION (f_001-f_022, full 66s). The top-right badge reads 'AI-generated · for educational illustration' on a drama, telling her for a full minute that this is a lesson.
9. 9. PACING IS RUSHED AT THE CLIMAX, THEN THE ENDING IS DEAD AIR. Twenty lines of ping-pong average 2.75s each, with uniform 199-394 ms gaps (audio_combine_recipe.gap_durations). audio_config asks for 'dramatic_pauses' and lists 'rushed_climax' under avoid, yet the climax (lines 16-19) runs 7.5s (47.9-55.4s). There are only 4 images in 66s, the last held for 20.7s, and 11s of it come after the final word.
10. 10. THE CAPTIONS DROP THE LAST LINE. Cue 20, 'Come on, Mara.', shows for 0.34s (53.518-53.856) while it is spoken until about 55.4s, so a muted viewer never reads the last line. Cues 7 and 19 display a bare '...' while the audio says something else.
11. 11. THE BURN SCAR READS AS A FRESH WOUND (img2-img4). The blueprint's 'burn scar under left eye' is drawn as bleeding red slashes that no line acknowledges, and it is absent or faint in img1. Minor.

**Verdict.** No. Nadia left at 0:02, when the man in the orange flight suit opened his mouth and a woman's voice came out. She might have forgiven that as a casting quirk if the pictures had held. They didn't: by 0:15 the keeper had aged ten years, grown a braid and changed into a knit cap, and by 0:45 the pilot was a different man with locs. She didn't FEEL the thing it was reaching for, a woman finally letting go of the post that killed her father. She only recognised that it was reaching, because the script says 'bittersweet acceptance' and the artifact delivers an ellipsis. That ellipsis is spoken by a third voice as a non-word, followed by 'Come on, Mara.' and eleven seconds of rain over a still image of two people standing exactly where they started. The pilot spends a minute arguing the light is life or death and then asks her to walk away from it. He tells her that leaving is how they 'honor' a man who stayed and drowned. Nobody talks like that, and nobody believes it at 1am. It also says 'for educational illustration' in the corner the whole time. It is not a story she'd keep. It is a storyboard with the wrong cast.

## Aarav (ESL / audio-first learner, Car-Mode)

Score 1/5, ship: **False**.

**Worst defect.** The Pilot, drawn as a man in all 22 frames and logged as MALE, speaks in a female voice (Inworld 'Naomi', measured 210 Hz on the clean stems) that is pitched HIGHER than the female Keeper (177.6 Hz), only ~32 Hz apart. By ear it is two women, the 'man' is the higher one, and I can't tell who is speaking. The pipeline's own gender gate reports 'clean'. On top of that, both '...' lines were voiced by a third fallback voice (OpenAI shimmer) as 0.6 s unvoiced hiss bursts at dialogue loudness that every ASR reads as 'It's stepped' / 'It's stuck', and the second burst lands on the climax.

**Ranked defects**

1. [10 Pilot lines, 2.13s-55.05s] WRONG VOICE FOR THE CHARACTER. The Pilot is drawn as a man in all 22 frames (f_001-f_022) and stamped gender=MALE in tts_segment_logs, but he is voiced by Inworld 'Naomi' (persona 'Isabella Crane', a female voice, cast pitch estimate 195 Hz). Measured on the clean per-segment stems with librosa pyin (median of per-segment medians): Pilot = 210.0 Hz (n=10 segments), Keeper 'Deborah' = 177.6 Hz (n=8). The 'man' has the HIGHER, female-register voice. Casting log, every role: 'character has no gender (raw=None) - refusing to guess'. The job's own listening QA reports gender 'clean'. False pass.
2. [ALL dialogue] TWO SAME-GENDER VOICES WITH NO CONTRAST. The Keeper/Pilot F0 separation is ~32 Hz on the clean stems (177.6 vs 210.0), under the ~40 Hz floor. On the master mix it reads ~13 Hz (191.5 vs 204.6), but music contaminates that reading. Hands-free there is no narrator and no speaker tag; the only cue to who is talking is the word 'Mara'. Two women trade 20 short lines and I can't tell who is talking.
3. [24.15-24.69s seg 6] and [52.63-53.12s seg 18] GARBLED TTS BURSTS. Both script lines are literally '...'. Inworld returned empty audio, and the pipeline fell back to a THIRD voice (OpenAI 'shimmer', rate 0.8) that voiced the ellipsis. Clean stems: 0.60 s and 0.58 s, 0 voiced pyin frames, zero-crossing rate 0.259 (2x the 0.123 of real speech), spectral centroid ~3550 Hz, as loud as dialogue (~-20 dB). Every ASR read hears words: whisper-small on the master gives 'It's stepped' / 'He stepped'; whisper-small on the isolated clips gives 'It's step' / 'is step'; the pipeline's own ASR gives 'It's stuck'. The Keeper's voice changes identity twice, and the second burst falls on the emotional climax right after 'we're going to honor that'.
4. [55.05-66.43s] 11.4 s OF STORYLESS TAIL after the last line. Per-second master level reaches -20.7 dB at 61 s, louder than the final spoken line (that line's window measures -21.5 dB, the quietest line in the episode). A Krumhansl key estimate on the music-only tail gives A major (r=0.88). The Lyria bed prompts read 'major key bright and warm ... uplifting', and direction_profile=explainer_info allows only 'energetic/bright/focused' tones. A storm drama meant to end in 'bittersweet acceptance' ends with 11 s of bright major-key music and no words, and in the car I'm left wondering whether the track froze.
5. [ALL turn gaps] METRONOMIC, EVENLY-METERED DELIVERY. audio_combine_recipe: pause_ms=300, with 19 gaps of 198-394 ms. The pipeline's own prosody_proxy measured a pause variance of 0.001 s^2 against a 0.05 s^2 floor and returned overall_ok=false ('metronomic'), but it is report_only, so the episode shipped. 'No.' (27.75s) arrives after the same polite gap as every other line: no interruption, no overlap, no held silence. The profile asked for 'tension ... urgency ... fast'; what I hear is two people reading turns off a card.
6. [53.518-53.856s, caption cue 20] SUBTITLE/AUDIO MISMATCH on the final line. The embedded mov_text cue 'Come on, Mara.' shows for 0.34 s while the spoken line runs 53.52-55.05 s (1.53 s). Cue 19 is a literal '...' caption over the shimmer hiss burst. Captions end at 53.86 s; the video runs to 66.43 s.
7. [47.90-55.05s] STORY DOES NOT RESOLVE BY EAR. The ask is 'the pilot must convince her to leave'. Audio-only, the Pilot first wants to fix the light ('I'm going down to the engine room'), then says 'come with me', then answers 'My father... he stayed' with 'we're going to honor that', which is the opposite of leaving. The ghost from the blueprint (her father drowned when a harbor light failed) is never said aloud, so the climax line has no meaning for a listener. The episode never says whether the light works, the boat is guided, or she leaves. The payoff beat is the hiss burst at 52.6s.
8. [PLAUSIBLE, needs ear check, 18.80-20.00s] 'I trim the wick': whisper-small transcribes 'I trend the wick'; the pipeline's ASR heard 'trim'. [28.36-30.29s] 'It's already flooding': the pipeline ASR split it as 'It's all ready. Flooding.', a possible stress or phrase-break error on 'already'. Two ASRs disagree, so I hold these as plausible mispronunciations, not confirmed.
9. [f_001-f_022, VISUAL CONTINUITY: the founder's standing complaint, NOT fixed] The Keeper has loose shoulder-length hair with no braid in f_001-f_005 (the blueprint says gray braid), then a braid plus headscarf in f_006-f_009, and the braid persists f_010-f_022. Her trousers change every image: khaki cargo in 1-5, mustard in 6-9, black in 10-15, olive in 16-22. The Pilot has short hair and a mustache in f_001-f_005, short hair and clean-shaven in f_006-f_015, and long locs with a different face in f_016-f_022, so the second half has a new pilot. The lamp is a different object in each image: Fresnel lens (1-5), a different lamp with a floor hatch (6-9), a pedestal oil lantern (10-15), none (16-22). The blueprint's burn scar under the left eye could not be verified at the 540x304 sample size.
10. [f_010-f_015, 27-45s] H1 SETTING CONTRADICTION. Sea water floods the lantern room at the top of the tower, with waves breaking around the pedestal, while the dialogue says the ENGINE ROOM below is flooding. [f_001-f_005, 0-2s] The Keeper holds a burning red flare while saying 'The red flare is still damp.' [f_016-f_022] She still holds the brass key after 'I'll take the key'.
11. [f_016-f_022, 45.7-66.4s] ONE STILL FOR 20.7 s, 11.4 s of it after the story ends. Only 4 distinct images in 66 s.
12. [f_001-f_022] Badge 'AI-generated · for educational illustration' on every frame of a fictional drama. Wrong label class for a story.
13. [metadata] script total_duration_seconds=200 against an actual 66.4 s. The job doc's script length is false.

**Verdict.** No. I'm driving and I hear two women; the one the picture calls the pilot is the higher voice. 'Who's talking?' I only know because one of them says 'Mara.' Twice the keeper turns into a different woman for half a second and hisses something like 'It's stepped'. The second time is the big emotional beat, and I don't get a word I can learn, just noise. Every line comes after the same 300 ms gap, so the 'urgent' storm sounds like two people reading turns. The story never tells me whether she leaves, and 'we're going to honor that' after 'he stayed' says the opposite of leaving. The captions flash the last line for a third of a second. Then the words stop and bright major-key music plays for 11 more seconds, louder than the last line, and I think the track froze. The words I can make out are pronounced well, but the cast is wrong, two lines are garbled, the end is dead air, and the meaning doesn't land on first listen. I would not keep this in my commute, and I would not share it.

## Ranked findings (synthesis)

1. 1. [CRITICAL, architectural] DRAMA CHARACTER IDENTITY NEVER REACHES VOICE CASTING, AND THE QA THAT SHOULD CATCH IT READS THE PIPELINE'S OWN LABEL.

Evidence:
- voice_cast.contract.voice_map is keyed only by Host1/Host2/Host3/Narrator. Every entry has requested_gender=None and fallback_flag='tier_default_no_archetype'.
- The script speaks as 'Lighthouse Keeper' / 'Pilot', and telemetry logs a 'voice_map miss'.
- voice_archetypes/resolver.py:441-452 receives gender=None and correctly refuses to guess. That pushes the cast into the legacy persona library, which assigns positionally: the Pilot landed on Host2, inworld/Naomi, which inworld/shared.py:338 registers as 'female'. The male voice Felix (Host3, ~115 Hz) went unused.
- tts_segment_logs then stamps the CHARACTER's gender (MALE) over the VOICE's real gender, and the listening-QA gender check reads that stamp and reports 'clean'.
- Both fallback personas carry the same 195 Hz pitch estimate, and persona_voice_ids_distinct=false, so nothing enforces vocal contrast.

This is a 'selected is not used' / split-identity seam: the blueprint knows the pilot is a man, and casting never receives that fact.

Blast radius:
- Every multi-character narrative whose speakers come from the story blueprint rather than HostN keys: drama, story, romance, and named-character dialogue and panel formats (Nadia, Aarav).
- Course and corporate role-plays (Elena) and mock-interview interviewer personas (Priya), if they route through the same host-slot contract.
- Car-Mode and audio-only surfaces worst of all.
- Every QA probe or judge that trusts tts_segment_logs.gender is reading a false label, so the defect is invisible fleet-wide.
2. 2. [CRITICAL, architectural] SILENT AND REACTION BEATS HAVE NO REPRESENTATION IN THE SCRIPT SCHEMA, AND THE TTS FALLBACK CHAIN DOES NOT PRESERVE A CHARACTER'S VOICE.

Evidence:
- The LLM is told to use 'Strategic use of ellipses (...) for pauses' in common/tts_expressions.py:570 and providers/openai_provider.py:1281. So it writes beats as dialogue text '...' (script lines 6 and 18).
- Those lines are sent to TTS as speech. Inworld returns empty audio, and the line drops to OpenAI tts-1 'shimmer' at rate 0.8: a different vendor and a different voice for the same character.
- The result is a 0.6s unvoiced burst at dialogue loudness (ZCR 0.259 vs 0.123 for real speech). ASR reads it as 'It's stepped' / 'He stepped' / 'It's stuck'.
- The caption track shows '...' over it.
- Line 18 is the protagonist's one surrender beat, the emotional climax.

There are two classes in one: (a) a pause is modelled as spoken text instead of a timed silence; (b) any primary-TTS empty response or error swaps the character's timbre mid-episode, with no voice-identity contract on the fallback.

Blast radius:
- Every drama, story and romance script, because the prompt invites ellipsis beats.
- Any line on any content type whose primary TTS fails, since the fallback crosses vendors and voices.
- Captions on watch, Theater and share.
3. 3. [HIGH, architectural, meta-class] DETECTED FAILURES SHIP, BECAUSE THE GATES ARE ADVISORY OR READ THE PIPELINE'S OWN CLAIMS.

On this one artifact:
- prosody_proxy measured pause variance 0.001 s^2 against a 0.05 floor and set overall_ok=false ('metronomic'). It is report_only, and the episode shipped.
- The voice_persona_match judge scored 4 and flagged below_floor. It is report_only.
- The gender gate checked a stamped label rather than the rendered voice, and reported 'clean' (see finding 1).
- The L0 acceptance gate's EDGE-CLIP check returned modality=None past the clip timeline and raised a MAJOR flag on frames that look correct by eye. That is a false positive, and it erodes trust in the one gate that does fire.
- The vision/adversary step was not run, so the gate verdict covers deterministic geometry only.

This is CLAUDE.md trap #4 observed live: a green gate is not a good artifact.

Blast radius: every content type. Any defect that one of these detectors already sees, including gender mis-cast, metronomic pacing and voice-persona mismatch, reaches users unchanged.
4. 4. [HIGH, architectural] THE SCRIPT GENERATOR AND ITS JUDGES ACCEPT A DRAMA WHOSE TURN IS STATED IN METADATA BUT MISSING FROM THE DIALOGUE.

Evidence:
- The episode_profile arc says 'bittersweet acceptance'.
- The stakes contradict themselves: 'The boat needs that light' at 34.3s is followed by 'Then come with me' at 39.9s.
- The father beat inverts. 'My father... he stayed' / 'we're going to honor that' praises staying while urging her to leave, and the pilot confirms family history he cannot know.
- The blueprint's ghost (her father drowned when a harbor light failed) is never said aloud.
- The protagonist's decision line is '...'.
- Nobody fixes the generator, the boat is dropped, and the 'I trim the wick' / 'it's electric now' setup is never paid off.
- The episode ends on 'Come on, Mara.' with no departure spoken or shown.

Nothing checks that the protagonist's decision is spoken or shown, that stakes set up are paid off, or that the persuader's argument holds together. At 1 minute, compression removes the turn entirely, and release_decision ships it anyway.

Blast radius: all drama, story and romance episodes, worst at 1-3 minutes, plus narrative social shorts (Sofia). Also any content judge that grades whether an arc label is present rather than whether the lines realise it.
5. 5. [HIGH, architectural, founder's standing complaint, so this is a REGRESSION WITNESS] SCENE IMAGES ARE GENERATED PER BEAT WITH NO LOCKED CHARACTER OR SET REFERENCE.

Keeper, across 4 images:
- Hair: loose with no braid (f_001-f_005, which contradicts the blueprint's gray braid), then braid plus knit cap, then braid.
- Apparent age: about +10 years in img2.
- Trousers: khaki, then mustard, then black, then olive.
- Burn scar: absent or faint, then drawn as bleeding fresh slashes.

Pilot: 3-4 different men. Short hair with a mustache, then clean-shaven, then a US-flag patch and name tag, then long locs with a mustache from 45.7s on.

Set: the lamp is a Fresnel lens beside a console, then a smaller lens with a floor hatch, then a portable lantern, then no lamp at all. The light the story is about is gone from the final picture.

Only 4 distinct images appear in 66s; the 8 clips are near-duplicate pairs.

Blast radius:
- Every narrative episode with recurring characters (Nadia).
- Course and class role-play visuals (Elena).
- Born-short social cuts with a recurring subject (Sofia).
- Both visual code paths (the episode visual director and born-short) must be checked.
6. 6. [MAJOR, architectural] THREE TIMELINES ARE NEVER RECONCILED: THE AUDIO MASTER, THE VISUAL CLIP TIMELINE AND THE CAPTION TRACK, PLUS AN UNVALIDATED DURATION IN METADATA.

Evidence:
- Speech ends at about 55.05-55.4s, the clip timeline ends at 55.174s, and the master runs to 66.43s.
- That leaves an 11.4s tail of music over a frozen still. The tail peaks at -20.7 dB at 61s, louder than the final spoken line (-21.5 dB).
- The final still holds for 20.7s.
- The last caption cue, 'Come on, Mara.', shows for 0.34s (53.518-53.856s) while the line is spoken for 1.53s. Cue ends are clamped to script boundaries, not to the rendered audio end, and the caption track stops 12.6s before the video.
- Script metadata says total_duration_seconds=200 against an actual 66.4s.
- The L0 EDGE-CLIP false positive comes from this same uncovered tail.

Blast radius:
- Every episode with an outro bed.
- The final caption of every episode, which matters for muted viewers on watch, Theater, share and social, and for ESL learners reading along.
- Every QA invariant that samples frames past the clip timeline.
- Anything that consumes the script's duration: progress, credit and cost estimates, and pacing checks.
7. 7. [MAJOR, architectural] THE FORMAT AND GENRE REGISTER DOES NOT PROPAGATE: THE USER'S STYLE OVERRIDES THE FORCED FORMAT.

Evidence:
- direction_profile=explainer_info, derived from style='Explainer', is applied to a job with format=drama.
- So the Lyria bed is prompted 'major key bright and warm ... uplifting', and the tail measures A major (r=0.88) on a 'bittersweet' storm ending.
- Every frame carries the disclosure badge 'AI-generated · for educational illustration'. That text is a single content-type-agnostic constant at stages/visuals/ai_disclosure.py:76.
- Changing the badge copy for fiction is a FOUNDER CALL, not an engineering fix: the constant's comment cites the founder's own wording, and it is what every user receives.

The genre decision has no single resolved contract that downstream stages consume, so music, overlays and pacing each infer genre on their own.

Blast radius: every drama or story job whose style selects a non-story profile, and every fiction video on watch, Theater, share and thumbnails.
8. 8. [MAJOR] PACING DIRECTIVES ARE SELECTED BUT NEVER CONSUMED.

Evidence:
- audio_config.emotional_arc asks for 'dramatic_pauses' and lists 'rushed_climax' under avoid.
- The combiner uses a single pause_ms=300 constant: 19 gaps of 198-394 ms, with no interruptions, no overlaps and no held silences.
- The climax runs 4 lines in 7.5s, and 'No.' at 27.75s arrives after the same polite gap as every other line.

Blast radius: every multi-speaker episode (drama, dialogue, panel), worst for tension and urgency profiles, and felt most by audio-only and Car-Mode listeners.
9. 9. [MAJOR, partly architectural] SCENE IMAGES ARE GROUNDED IN THE BEAT'S MOOD, NOT IN WHAT THE SPOKEN LINE STATES, AND ATMOSPHERE TOKENS ARE APPLIED EVERYWHERE.

Evidence:
- 'The red flare is still damp' plays over a burning flare (f_001-f_005).
- 'I'll take the key. You take my hand.' plays over an image where the keeper holds the key. The story's only gesture of trust is drawn backwards.
- The engine room flooding is drawn as surf around their ankles in the lantern room at the top of the tower (f_010-f_015).
- Rain streaks fall indoors in all 22 frames, and waves sit at window height.
- A clip that spans 4-7 lines can match at most one of them, and nothing checks the image against the line it covers.

Blast radius: every scene_image beat in narrative content, plus explainer and course visuals, where the image must match the sentence being spoken. Weather- or mood-heavy stories (storm, snow, night) are hit hardest.
10. 10. [MINOR, needs an ear check] Two possible pronunciation errors. 'I trim the wick' (18.8-20.0s) is heard as 'trend' by whisper-small but as 'trim' by the pipeline's ASR. 'It's already flooding' (28.4-30.3s) may carry a phrase break inside 'already'. The two ASRs disagree, so these are held as PLAUSIBLE, not confirmed.

## Top architectural fix (synthesis)

Make CHARACTER a first-class identity contract that runs end to end through the pipeline. Today it is a name string in the script plus four independent guesses: one by casting, one by the TTS fallback, one by the image prompts and one by QA.

The contract is resolved ONCE from the story blueprint and keyed by the exact speaker name the script emits, so 'Pilot' is the key, not 'Host2'. It carries:
- gender, age range, and the voice_id cast under a hard gender filter plus a pitch/timbre contrast constraint across the cast;
- a same-character fallback voice: same gender and near the same pitch, or the line fails loudly rather than swapping vendor and voice;
- a visual identity sheet (face, hair, wardrobe, defining marks) with a reference image that every scene_image generation is conditioned on;
- a set sheet for recurring locations and props.

Every downstream stage READS this contract and never re-derives it:
- voice casting, through voice_archetypes/resolver.py, which today receives gender=None and correctly refuses to guess;
- the TTS fallback chain;
- scene-image prompts;
- the caption speaker track;
- QA, which must check the RENDERED voice's measured gender and F0 and the rendered image's identity against the contract, never the pipeline's own stamped label.

Why this one fix has the most leverage:
- It removes both personas' worst defect, the male pilot voiced by female Naomi.
- It removes the cross-vendor voice swap on the '...' lines.
- It removes the missing speaker contrast that Aarav cannot hear through.
- It removes the founder's standing continuity complaint: 4 keepers, 4 pilots, 4 lamps.
- It turns the false-'clean' gender gate into a real instrument.

It covers drama, story, role-play, mock-interview and dialogue formats on both visual code paths. Typing silence as a first-class beat (no TTS, a timed gap, no caption text) is the natural second field on the same script schema change, so the '...' class closes in the same move.

## Transcript excerpt (as observed)

```
USER ASK / BRIEF:
- topic: "the tension between a lighthouse keeper and the pilot who must convince her to leave before the storm"
- inputs: duration_min=1.0, style="Explainer", mode="podcast", language=en-US, skip_clarifier=true, custom_instructions=null
- Resolved: format=drama, audio_format=drama, content_type=storytelling, genre=drama, modality_policy=scene_images, quality_tier=low, subscription_tier=ultimate
- Cast: Lighthouse Keeper ("Mara"), Pilot
- Story blueprint (preferences._story_blueprint): the keeper is in her late 50s, with a gray braid and a burn scar under her left eye. Her ghost is the night her father drowned when a harbor light failed. She wants to keep the beacon burning; what she needs is to trust someone else and leave her post.
- Episode profile: arc runs from tension and reluctance through urgency and resolve to bittersweet acceptance; pacing fast.

NARRATION TRANSCRIPT (whisper small, run on the surfaced master episode_video.mp4; a separate pass on the 51s+ tail recovers the final line):
[00.00-02.12] Keeper: The red flare is still damp.
[02.12-07.64] Pilot: Mara, the storm's closing in. That beam has to guide the evacuation boat.
[07.64-11.80] Keeper: It will. The lens is cleared. Pressure's steady.
[11.80-16.00] Pilot: The generator's failing. I saw the indicator before I landed.
[16.00-20.00] Keeper: It's always failing. I trend [script: trim] the wick.
[20.00-24.16] Pilot: Wick? Mara, it's electric now. The backup relay is shot.
[24.16-25.16] Keeper: "It's stepped." [script line is '...']
[25.16-27.76] Pilot: I'm going down to the engine room.
[27.76-28.36] Keeper: No.
[28.36-30.60] Pilot: It's already flooding.
[30.60-34.28] Keeper: Then stay out of it.
[34.28-38.32] Pilot: The boat needs that light. They're relying on us.
[38.32-39.92] Keeper: I know.
[39.92-41.36] Pilot: Then come with me.
[41.36-45.72] Keeper: The brass key? It's still in the lock.
[45.72-47.92] Pilot: I'll take the key. You take my hand.
[47.92-49.84] Keeper: My father, he stayed.
[49.84-52.68] Pilot: He did. And we're going to honor that.
[52.68-53.52] Keeper: "He stepped" / "You step" [script line is '...']
[~53.5-55.4] Pilot: Come on, Mara.
[55.4-66.4] No speech. Non-speech audio continues: per-second mean volume ranges from -22 to -31 dB (ffmpeg volumedetect).

The script (outputs.script, 20 lines) matches word for word apart from the stutters ("The— the", "Wick?— wick?") and the two '...' lines. Script metadata claims total_duration_seconds=200; the actual length is 66.4s. Captions VTT has 20 cues ending at 53.856s.
```
