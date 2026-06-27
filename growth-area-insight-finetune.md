# Growth Area Insight — Fine-Tuned Model Implementation Note

> Handoff note for an AI code editor. Goal: replace the third-party LLM call
> (Claude Haiku) that scores the **Growth Area** insight with a self-hosted,
> **fine-tuned model** that takes a user's profile/progress data and returns the
> six growth-area scores as strict JSON.

---

## 1. What this insight is

The **Growth Area** card on the Insights screen shows six personal-growth
dimensions, each scored `0–100`. The score is meant to represent the user's
**current growth focus, readiness, and available signal** — it is explicitly
**not a diagnosis or clinical assessment**.

The six areas (canonical, fixed order and ids):

| id                     | title                | short title    | icon |
| ---------------------- | -------------------- | -------------- | ---- |
| `mental_health`        | Mental Health        | Mental Health  | 😌   |
| `growth_mindset`       | Growth Mindset       | Growth Mindset | 🧠   |
| `relationships`        | Relationships        | Relationships  | ❤️   |
| `personal_development` | Personal Development | Personal Dev   | 🌱   |
| `self_awareness`       | Self-awareness       | Self-awareness | 🧘   |
| `stress_management`    | Stress Management    | Stress Mgt     | 💆   |

The card renders these as a progress grid or a radar chart
(`lib/features/insights/widgets/growth_area_card.dart`).

---

## 2. Where it lives today

| Layer            | File                                                        | Role                                                                                       |
| ---------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| UI               | `lib/features/insights/widgets/growth_area_card.dart`       | Renders six scores as progress rings / radar.                                              |
| Client service   | `lib/features/insights/services/growth_area_service.dart`   | Calls the edge function; has a local heuristic fallback; defines `GrowthAreaInsight` model. |
| Edge function    | `supabase/functions/growth-area-insights/index.ts`          | Fetches user data, runs a **Claude** call, with a deterministic heuristic fallback.        |

### Current flow

1. Client invokes the `growth-area-insights` Supabase edge function.
2. Edge function loads the user's data, computes a **deterministic heuristic
   score** (`scoreFromProfile`), then asks **Claude Haiku**
   (`claude-haiku-4-5-20251001`) to refine it (`maybeScoreWithClaude`).
3. If Claude is unavailable / returns bad JSON, it falls back to the heuristic.
4. If the edge function fails entirely, the Dart client computes its own local
   heuristic (`_scoreFromProfile`), and finally falls back to static defaults.

**The fine-tuned model replaces step 2's Claude call.** Everything else (data
loading, fallbacks, normalization, output contract) stays the same.

---

## 3. Model input

The model receives a single JSON object built from three Supabase tables. This
is the exact payload shape already constructed in `maybeScoreWithClaude`:

```json
{
  "goals": ["thoughts that loop", "work stress"],
  "stress_frequency": "a few times a week",
  "happiness_level": "catching myself before i react",
  "ai_onboarding_responses": [
    { "question": "...", "answer": "..." }
  ],
  "progress_metrics": {
    "loops_identified": 0,
    "top_loops": [],
    "trigger_clarity_pct": 0,
    "reaction_autopilot_pct": 0,
    "interrupt_rate_pct": 0,
    "ladder_stage": "spotter"
  },
  "user_memory": {
    "entries_count": 0,
    "active_loops": [],
    "emotion_distribution": {},
    "narrative_summary": "..."
  },
  "fallback_scores": [
    { "id": "mental_health", "value": 42 },
    { "id": "growth_mindset", "value": 42 },
    { "id": "relationships", "value": 42 },
    { "id": "personal_development", "value": 42 },
    { "id": "self_awareness", "value": 46 },
    { "id": "stress_management", "value": 42 }
  ]
}
```

### Data sources

- **`users`** table: `goals`, `stress_frequency`, `happiness_level`,
  `ai_onboarding_responses`.
- **`progress_metrics`** table: `loops_identified`, `top_loops`,
  `trigger_clarity_pct`, `reaction_autopilot_pct`, `interrupt_rate_pct`,
  `ladder_stage`.
- **`user_memory`** table: `entries_count`, `active_loops`,
  `emotion_distribution`, `narrative_summary`.

### Input hygiene (already implemented, keep it)

- `ai_onboarding_responses`: cap to 8 items; `question` ≤ 280 chars,
  `answer` ≤ 700 chars.
- `user_memory.narrative_summary`: ≤ 600 chars; `active_loops`: ≤ 5 items.
- `fallback_scores` are passed in so the model has a sane anchor when signal is
  thin.

### Domain glossary (Patternverse concepts the model must understand)

- **Loop**: a repeating thought/behavior pattern the user is tracking.
- **Ladder stage**: the user's progression — `spotter → namer → mapper →
  interruptor → rewriter → stabilizer` (increasing mastery).
- **`trigger_clarity_pct`**: how clearly the user identifies what triggers a loop.
- **`interrupt_rate_pct`**: how often they interrupt a loop before reacting.
- **`reaction_autopilot_pct`**: how often they react on autopilot.

---

## 4. Model output (strict contract — do not change)

Return **only** valid JSON, all six ids exactly once, whole-number `value`
`0–100`:

```json
{
  "areas": [
    { "id": "mental_health", "value": 0 },
    { "id": "growth_mindset", "value": 0 },
    { "id": "relationships", "value": 0 },
    { "id": "personal_development", "value": 0 },
    { "id": "self_awareness", "value": 0 },
    { "id": "stress_management", "value": 0 }
  ]
}
```

Rules (carried over from the current system prompt):

- Include all six ids exactly once.
- Values are `0–100` whole numbers.
- Scores represent current growth focus, readiness, and available signal — **not
  a diagnosis or clinical assessment**.
- Use **only** the provided data. Do **not** infer protected traits, trauma
  history, or medical conditions.
- If data is thin, stay **moderate** (near the fallback) rather than overconfident.
- Downstream code clamps the final value to `[0, 100]` and, in the heuristic
  path, to `[32, 94]`. The model should naturally produce mid-range values for
  sparse inputs.

---

## 5. The scoring logic to learn (this is your ground truth)

The deterministic heuristic in both `index.ts` (`scoreFromProfile` /
`applyProgressMetrics`) and `growth_area_service.dart` (`_scoreFromProfile` /
`_applyMetrics`) encodes the domain rules. Use it as the **label generator** and
as a behavioral spec. Summary of the rules:

**Base scores:** all areas start at `42`, except `self_awareness` at `46`.

**Goal phrases (substring match, additive):**

- `"thoughts that loop"` → mental_health +14, self_awareness +11, stress_management +12
- `"reactions you can't explain"` → self_awareness +14, stress_management +8, growth_mindset +6
- `"relationship"` → relationships +16, self_awareness +6, mental_health +4
- `"work"` → stress_management +12, personal_development +8, mental_health +6
- `"decision"` → growth_mindset +12, personal_development +10, self_awareness +8
- `"don't know"` / `"not sure"` → self_awareness +10, growth_mindset +8

**`stress_frequency`:**

- `almost every day` → stress_management +16, mental_health +10, self_awareness +5
- `a few times a week` → stress_management +12, mental_health +7
- `now and then` → stress_management +6
- `i don't know yet` → self_awareness +8, growth_mindset +5

**`happiness_level`:**

- `noticing something i couldn't see alone` → self_awareness +12, growth_mindset +5
- `catching myself before i react` → stress_management +12, growth_mindset +8
- `understanding someone in my life better` → relationships +14, self_awareness +6
- `making sense of a decision` → personal_development +12, growth_mindset +8
- `somewhere honest to think` → mental_health +8, self_awareness +8

**Keyword density** (across goals + answers + memory text), `+4` per matched
keyword, capped at `+22` per area. Keyword lists per area are defined in the
source (anxiety/mood → mental_health, stuck/grow/learn → growth_mindset,
partner/family/friends → relationships, decision/habit/career →
personal_development, pattern/notice/trigger → self_awareness,
stress/burnout/deadline → stress_management).

**Engagement depth:** if there are `ai_onboarding_responses`, add
`min(8, count*2)` to self_awareness and `max(3, depthBoost/2)` to growth_mindset.

**Progress metrics (take max with current, additive where noted):**

- `trigger_clarity_pct` raises `self_awareness` to at least that value.
- `interrupt_rate_pct` raises `stress_management` to ≥ that value, `growth_mindset` to ≥ value−4.
- `loops_identified` → self_awareness `+min(12, n*2)`, growth_mindset `+min(8, n)`.
- `ladder_stage` floor: spotter 48, namer 56, mapper 64, interruptor 72, rewriter 80, stabilizer 88 → raises growth_mindset to ≥ stage, self_awareness to ≥ stage−4.

**Final clamp:** heuristic clamps each area to `[32, 94]`.

> The fine-tuned model should reproduce these tendencies **and** generalize to
> free-text answers that keyword matching misses (the whole reason Claude was
> added). Treat the heuristic as the floor of quality, not the ceiling.

---

## 6. Fine-tuning plan

### 6.1 Task framing

Single-turn, instruction-following, **structured JSON output**. Input = the
payload from §3, output = the JSON from §4. No chat history.

### 6.2 Building the dataset

Combine three sources so the model learns both the rules and the nuance:

1. **Heuristic labels (cheap, large):** Run the existing `scoreFromProfile`
   over many synthetic/real-anonymized profiles to auto-label thousands of
   examples. Guarantees rule coverage.
2. **Distillation from Claude (nuance):** For a subset, capture Claude's refined
   outputs (same prompt as today) as targets so the model learns to read
   free-text answers, not just keywords.
3. **Hand-curated edge cases:** thin data (stay moderate), contradictory
   signals, multilingual/typo'd answers, empty `progress_metrics`/`user_memory`,
   long narratives. Include cases that test the "don't infer protected traits"
   guardrail.

Aim for balanced coverage across all six areas and across `ladder_stage` values.
Hold out ~10–15% for eval, stratified by data richness.

### 6.3 Training example format

Use chat/instruction format with the existing system prompt verbatim:

```json
{
  "messages": [
    { "role": "system", "content": "<the growthPrompt from index.ts>" },
    { "role": "user", "content": "<stringified input payload from §3>" },
    { "role": "assistant", "content": "{\"areas\":[{\"id\":\"mental_health\",\"value\":58}, ...]}" }
  ]
}
```

### 6.4 Base model + method

- Prefer a **small instruction-tuned open model** (e.g. a 1–8B class model) so it
  can run cheaply server-side. The task is narrow and structured.
- **LoRA/QLoRA** is sufficient; full fine-tune is overkill.
- Enforce JSON with constrained/grammar-based decoding (e.g. JSON schema /
  grammar) at inference so output is always parseable.
- Keep `max_tokens` small (current call uses 500); output is tiny.

### 6.5 Evaluation

Report on the held-out set:

- **Schema validity:** 100% parseable, all six ids present exactly once,
  integers in `[0, 100]`.
- **Accuracy vs. labels:** MAE per area and overall (target MAE comfortably
  below the heuristic's spread; e.g. ≤ ~6 points).
- **Monotonicity / sanity checks:** higher `ladder_stage` ⇒ ≥ growth_mindset;
  higher `trigger_clarity_pct` ⇒ ≥ self_awareness; thin data ⇒ values near
  fallback.
- **Guardrail checks:** never returns protected-trait inferences; degrades to
  moderate on empty input.

### 6.6 Integration

In `supabase/functions/growth-area-insights/index.ts`:

- Replace `maybeScoreWithClaude` with `maybeScoreWithFineTunedModel` that POSTs
  the same `body` to the new inference endpoint.
- Keep the **same system prompt**, the **same `normalizeAreas` post-processing**,
  and the **same fallback to `scoreFromProfile`** on any failure or invalid JSON.
- Keep `source` in the response (`'finetuned' | 'profile'`) for observability.
- No client changes required — `growth_area_service.dart` already consumes
  `{ "areas": [...] }` and clamps to `[0, 100]`.

### 6.7 Guardrails to preserve

- On any inference error, timeout, or non-conforming output → fall back to the
  heuristic. Never block the card from rendering.
- Validate output server-side before returning (ids, count, ranges).
- Do not log raw onboarding free-text in plaintext beyond what's already done;
  this is sensitive personal data.

---

## 7. Acceptance criteria

- [ ] New inference endpoint returns the §4 JSON contract for the §3 input.
- [ ] Edge function uses the fine-tuned model in place of Claude, with the
      heuristic fallback intact.
- [ ] 100% schema-valid outputs; all six ids; integer values `0–100`.
- [ ] Eval MAE vs. labels at or below target; sanity/guardrail checks pass.
- [ ] No client-side changes needed; the card renders identically.
- [ ] Latency and cost ≤ the current Claude call.

---

## 8. Quick reference — files to touch

- `supabase/functions/growth-area-insights/index.ts` — swap the model call,
  reuse prompt + `normalizeAreas` + fallback.
- (No change) `lib/features/insights/services/growth_area_service.dart` — consumes
  `areas`, clamps `[0,100]`, has its own local heuristic fallback.
- (No change) `lib/features/insights/widgets/growth_area_card.dart` — renders.
- Add: training data export script (reuse `scoreFromProfile` logic) + fine-tune
  config + inference service.
