# KitesForU Content Quality Standards

## Purpose
This document defines quality expectations for all generated content across the platform.
Each content type and pipeline stage has explicit pass/fail criteria that can be validated
either manually or through automated testing.

---

## 1. Content Categories

Content falls into three categories that determine pipeline behavior:

| Category | Examples | Research | Voice | Emotions |
|----------|----------|----------|-------|----------|
| **Educational** | Explainer, Deep Dive, How-To | Web research required | Clear, instructional | helpful, confident, curious |
| **Creative/Fiction** | Storytelling, Horror, Romance, Comedy, Sci-Fi | NO web research (creative synthesis) | Genre-appropriate narrative | Genre-specific palette |
| **News/Current** | News digest, Current events, Interview | Current news research | Professional, measured | thoughtful, concerned, neutral |

**Critical Rule**: A romantic story MUST NOT trigger news research. A horror series MUST NOT
get educational learning objectives. Content category determines the entire pipeline behavior.

---

## 2. Research Stage Quality

### 2.1 Educational Content
- **MUST** perform web research via Tavily/search tools
- **MUST** use topic="general" or topic="news" based on topic timeliness
- **MUST** produce factual, source-backed synthesis
- **MUST NOT** hallucinate sources or statistics

### 2.2 Creative/Fiction Content
- **MUST NOT** perform news research regardless of keywords in topic
- **MUST** use topic="general" if any research is needed
- **SHOULD** focus on narrative techniques, genre conventions, storytelling patterns
- Keywords like "recent", "latest", year references (2024, 2025) MUST NOT trigger news search
- Tone MUST NOT be hardcoded to "professional" — should match content style

### 2.3 News/Current Content
- **MUST** perform news research with appropriate time_range
- **MUST** use topic="news" with recent time filters
- **MUST** ground content in verifiable current events

---

## 3. Curriculum/Syllabus Stage Quality

### 3.1 Educational Courses
- Episodes have learning objectives and key topics
- Structured progression (fundamentals → intermediate → advanced)
- No forced emotional narratives on technical/factual topics

### 3.2 Storytelling/Creative Courses
- Episodes have narrative goals, not learning objectives
- Titles are evocative, genre-appropriate (not "Introduction to...")
- Story arc follows genre conventions (horror: build dread, comedy: setup+payoff)
- No educational framing forced onto fiction

### 3.3 Classes (K-12)
- Age-appropriate language and complexity
- Clear learning objectives per lesson
- NO emotional tangents, motivational asides, or philosophical digressions
- Content stays directly on-topic for stated subject and grade level

---

## 4. Script/Dialogue Stage Quality

### 4.1 Speaker Roles by Format

| Format | Speakers | Roles | Voice Dynamic |
|--------|----------|-------|---------------|
| DIALOGUE (educational) | Host1 + Host2 | Host1=driver, Host2=curious questioner | Complementary |
| DIALOGUE (debate) | Host1 + Host2 | Equal authority, opposing views | Balanced |
| DIALOGUE (story) | Host1 + Host2 | Host1=storyteller, Host2=engaged listener | Narrator+Audience |
| NARRATION | Narrator | Single voice, characters via embedded dialogue | Dramatic |
| MONOLOGUE (meditation) | Host | Single calm voice | Soothing |
| MONOLOGUE (motivational) | Host | Single energetic voice | Inspiring |
| INTERVIEW | Interviewer + Guest | Different authority levels | Question+Expert |
| MULTI_VOICE | 3+ speakers | Distinct character voices | Ensemble |

### 4.2 Speaker Voice Distinctness
- Host1 and Host2 MUST use different TTS voices (alloy vs echo on OpenAI)
- Speaker names MUST be "Host1"/"Host2" (not character names, not "Speaker 1")
- When content has 2+ speakers, they MUST sound like different people
- Single-speaker formats (NARRATION, MONOLOGUE) use one consistent voice

### 4.3 Emotion by Genre

| Genre | Core Emotions | Forbidden Emotions |
|-------|--------------|-------------------|
| Horror | suspenseful, eerie, tense, dread, mysterious | excited, playful, warm |
| Comedy | comedic, witty, playful, sarcastic, gleeful | somber, dread, clinical |
| Romance | tender, passionate, warm, wistful, longing | clinical, calculated, tense |
| News/Documentary | thoughtful, serious, concerned, measured | excited, amazed, playful |
| Educational | confident, curious, helpful, clear | dread, eerie, passionate |
| Meditation | soothing, gentle, peaceful, calm | excited, tense, comedic |
| Motivational | excited, confident, warm, determined | calm (forced), soothing (forced) |

### 4.4 Monologue Format Rules
- Meditation/bedtime monologues: calm, soothing, intensity 0.2-0.5
- Motivational monologues: energetic, confident, intensity 0.6-0.9
- Storytelling monologues: dramatic range, intensity varies with narrative
- The format MUST NOT force calm on all monologue content

---

## 5. Audio Stage Quality

### 5.1 Voice Assignment
- Different speakers MUST have different voices
- Voice selection respects speaker role (Host1, Host2, Narrator, Guest)
- Speaker normalization correctly maps LLM output to voice config

### 5.2 Musical Intro
- Genre-appropriate ambient intro (12s default)
- TTS tagline with show title
- Crossfade transition to main content
- Intro MUST NOT be harsh beeps or raw sine waves

---

## 6. Writeup Stage Quality

### 6.1 Research Integrity
- Writeup research MUST use actual web search tools (not LLM hallucination)
- Sources must be real, verifiable URLs
- Statistics and claims must be grounded in search results
- Creative writeups (fiction) skip research entirely

### 6.2 Format-Specific Standards

| Format | Target Length | Key Requirements |
|--------|-------------|-----------------|
| Blog Post | 1500-2500 words | H2/H3 headers, FAQ section (3-5 Q), source attribution, primary keyword in first 100 words |
| Newsletter | 200-500 words | Subject line (40-50 chars), preheader (100-125 chars), single CTA, scannable sections |
| Twitter Thread | 5-8 tweets | Each tweet <280 chars, 1/ numbering, hook tweet, standalone tweets |
| LinkedIn Post | 1300-1600 chars | Hook <210 chars (before "...More"), whitespace formatting, ends with question, 3-5 hashtags |
| Show Notes | 600 words | Episode overview, 5-7 takeaways, notable insights, resources. Timestamps only for podcast exports. |
| Script/Transcript | varies | Full dialogue with speaker labels and stage directions |
| Social Media Post | 100-300 words | Platform-appropriate formatting and engagement hooks |

### 6.3 Voice Calibration
- Each writeup MUST have tone matching its topic and audience
- Technical topics: authoritative, precise vocabulary
- Lifestyle topics: warm, conversational, accessible
- Same topic ≠ same voice for different audiences

---

## 7. Smart Create Quality

### 7.1 Content Category Detection
- System MUST distinguish educational vs creative vs news content
- Template hints MUST communicate processing_type (fiction/educational/news)
- Chat system prompt MUST NOT promise research for creative content
- Confidence scoring MUST account for content type (creative needs more specification)

### 7.2 Style vs Content Category
- Style = how it sounds (Explainer, Storytelling, Interview, Motivational)
- Content Category = what it is (educational, fiction, news)
- These are orthogonal and MUST be tracked separately
- Storytelling style + fiction category → skip research, creative synthesis
- Explainer style + news category → research current events

---

## 8. Class-Specific Quality

### 8.1 Style Passthrough
- Class audio MUST respect the course's style setting
- If course is Storytelling, class lessons MUST use Storytelling style (not Explainer)
- AudioProcessor MUST pass style from class_params, not hardcode "Explainer"

### 8.2 Voice/Speaker Guidance
- Class audio includes speaker voice guidance based on style
- Storytelling: narrative voice with character and emotional tone
- Motivational: encouraging, energetic, coaching tone
- Explainer: clear, instructional, professional tone

---

## 9. Anti-Patterns (MUST NOT)

1. **MUST NOT** research news for fiction/creative content
2. **MUST NOT** hardcode tone="professional" for all content types
3. **MUST NOT** force calm emotions on motivational monologues
4. **MUST NOT** use educational framing for storytelling content
5. **MUST NOT** ignore course style in class audio generation
6. **MUST NOT** hallucinate research sources (must use actual search tools)
7. **MUST NOT** apply generic emotions to genre-specific content
8. **MUST NOT** generate show notes with fake timestamps for standalone writeups
9. **MUST NOT** treat all content types identically in confidence scoring
10. **MUST NOT** use words instead of characters for Twitter/LinkedIn length targets
