# Patternverse API — Integration Guide (for the Flutter client)

This is the consumer-facing reference for the **Patternverse Mirror Intelligence
System (MIS) API**. It walks the full journey a user takes in the app:

1. **Session chat** — a turn-based reflective conversation that, once a genuine
   repeating pattern emerges, returns a structured **synthesis** ("the mirror").
2. **Patterns** — the list of confirmed patterns a user has accumulated.
3. **Growth Area insight** — six personal-growth scores (`0–100`) rendered on the
   Insights screen, scored from the user's profile + progress data.

> Audience: the code/editor wiring up the Flutter app. Everything here is about
> **how to call the endpoints and what comes back** — not internal model details.
> For running/deploying the server, see [`README.md`](./README.md).

---

## Base URL & conventions

| | |
|---|---|
| **Local base URL** | `http://localhost:8000` (or whatever `--port` you ran) |
| **Interactive docs** | `GET /docs` (Swagger UI — try every endpoint live) |
| **Content type** | `application/json` for all request bodies |
| **Auth** | None today. CORS is open (`*`). Don't ship secrets to the client. |
| **IDs** | `user_id` is a string (a Supabase `users.user_id` UUID in prod). `session_id` is a UUID returned by `/session/start`. |
| **Timestamps** | ISO-8601 strings (e.g. `2026-06-27T19:51:31.423000+00:00`). |

### Error shape

Errors come back as FastAPI's standard envelope:

```json
{ "detail": "Session not found" }
```

| Status | When | What the client should do |
|--------|------|---------------------------|
| `404` | Session not found | Treat the session as gone; start a new one. |
| `409` | Replying to a session that ended without a pattern | The session is closed; start a new one. |
| `422` | Request body failed validation | Fix the payload (missing/!wrong-typed fields). |
| `503` | Model backend unavailable (chat only) | Show a retry affordance. |
| `500` | Storage/DB error | Retry; surface a generic error. |

> The **Growth Area** endpoint never returns `503` for a model outage — it falls
> back to a deterministic heuristic and still returns `200` (see below).

---

## Endpoint map

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/health` | Liveness + which environment/model is active |
| `POST` | **`/mirror/chat`** | **Mirror chat — start or send the next message (recommended)** |
| `GET`  | `/mirror/chat/{session_id}` | Full mirror chat state (messages + pattern) |
| `POST` | `/mirror/chat/end` | End chat; force synthesis if enough signal exists |
| `POST` | `/session/start` | Begin a session (legacy alias) |
| `POST` | `/session/reply` | Send a user message (legacy alias) |
| `GET`  | `/session/{session_id}` | Full session state (legacy alias) |
| `POST` | `/session/end` | End session (legacy alias) |
| `GET`  | `/patterns/{user_id}` | All confirmed patterns for a user |
| `POST` | `/insights/growth-area` | Score the six growth areas for the Insights screen |

---

# 1. Mirror chat (recommended for the Flutter app)

The **Chat** tab runs the Mirror Intelligence System — a turn-based reflective
conversation. Once a genuine repeating pattern emerges, the mirror returns a
structured **synthesis** (`PatternObject`).

### One endpoint for the chat screen

Use **`POST /mirror/chat`** for both starting and continuing a conversation.

```
POST /mirror/chat  { "user_id": "..." }                    → opening + session_id
POST /mirror/chat  { "session_id": "...", "message": "…" } → question or synthesis
(repeat until session_complete == true, or POST /mirror/chat/end)
```

| Turn | Server behavior |
|------|-----------------|
| 1–5  | Questions only — never synthesizes this early. |
| 6–9  | Synthesizes **if** a clear repeating pattern is visible, else asks again. |
| 10+  | Forces a synthesis. |

Branch on `type` in the response — do not hard-code turn logic on the client.

---

## `POST /mirror/chat` — start

**Request**
```json
{ "user_id": "demo-user" }
```

Do **not** send `session_id` or `message` when starting.

**Response `200`**
```json
{
  "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f",
  "type": "opening",
  "content": "Think of a recent moment where you reacted in a way that surprised you. What happened?",
  "turn": 1,
  "session_complete": false
}
```

Store `session_id`. Render `content` as the first assistant bubble.

### ⚠️ `user_id` must already exist in prod (onboarding ordering)

In production (Supabase active), `sessions.user_id` is a **`NOT NULL` foreign key
to `public.users(user_id)`** (and so is `patterns.user_id`). So the `user_id` you
pass when starting **must already be a real row in `public.users`** —
otherwise the insert fails the FK constraint and the API returns **`500`** (it
does *not* silently fall back to questions; that fallback would be the app's own
local handling of the error). In local/in-memory mode any string works.

This matters because the account is normally created at the **end** of
onboarding — so if you run the Mirror chat *during* onboarding, the user doesn't
exist yet.

**Required pattern: create the identity up front, upgrade it later.**

1. At the **start** of onboarding, create the user so a `public.users` row
   exists — e.g. Supabase **anonymous sign-in**
   (`supabase.auth.signInAnonymously()`), ensuring a matching `public.users` row
   is created (via your signup trigger, or an explicit insert). Use that user's
   **UUID** as `user_id`.
2. Run the Mirror chat normally — sessions/messages/patterns now persist against
   that UUID.
3. At the **end** of onboarding, **upgrade** the anonymous user into a permanent
   account (attach email/password). The **UUID is stable**, so every session and
   pattern created during onboarding stays linked to the now-permanent account —
   no migration/relinking needed.

> Plan a periodic cleanup for anonymous users who abandon onboarding (the
> `on delete cascade` FKs will drop their sessions/messages/patterns with them).

---

## `POST /mirror/chat` — reply

**Request**
```json
{
  "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f",
  "message": "I went quiet the moment my partner asked what was wrong."
}
```

**Response — still asking (`type: "question"`)**
```json
{
  "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f",
  "type": "question",
  "content": "What did going quiet protect you from in that moment?",
  "turn": 3,
  "session_complete": false
}
```
Here `content` is a **plain string** — render it as the next assistant bubble.

**Response — pattern found (`type: "synthesis"`)**
```json
{
  "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f",
  "type": "synthesis",
  "content": {
    "pattern_name": "Proximity Withdrawal Loop",
    "pattern_summary": "When closeness asks something of you, you go quiet to stay safe — which creates the distance you feared.",
    "trigger": "A partner reaching toward you emotionally.",
    "response": "You withdraw and go silent.",
    "insight": "The silence isn't absence — it's a guard you post when intimacy feels like exposure.",
    "next_step": "Next time you feel the pull to go quiet, you could name the urge out loud instead."
  },
  "turn": 7,
  "session_complete": true
}
```
Here `content` is a **`PatternObject`**. When `session_complete` is `true`, stop
the loop and show the synthesis card.

> **Key client rule:** `content` is a string when `type` is `"opening"` or
> `"question"`, and an object when `type` is `"synthesis"`. Switch on `type`
> before parsing `content`.

**Idempotency / edge cases**
- Replying to an already-complete session returns the **same synthesis** again
  (`session_complete: true`).
- If the session ended *without* a pattern, reply returns `409`.

---

## `GET /mirror/chat/{session_id}`

Rehydrate the conversation when the user reopens the Chat tab.

Same shape as `GET /session/{session_id}` — see [Legacy session routes](#2-legacy-session-routes) below.

---

## `POST /mirror/chat/end`

**Request**
```json
{ "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f" }
```

Same behavior as `POST /session/end` — force synthesis when `turn >= 6`, else
close without fabricating a pattern.

---

# 2. Legacy session routes

The `/session/*` paths behave identically to `/mirror/chat` and remain for
backward compatibility. New Flutter code should prefer `/mirror/chat`.

### Lifecycle (legacy)

```
POST /session/start
        │  → opening question (turn 1)
        ▼
┌──► POST /session/reply  (send user_message)
│        │
│        ├─ type:"question"   → keep the loop going (turn++)
│        └─ type:"synthesis"  → pattern returned, session_complete:true  ──► done
│
└──(repeat until synthesis, or call /session/end to force/close)
```

### Turn rules (so the client can set expectations)

| Turn | Server behavior |
|------|-----------------|
| 1–5  | Questions only — never synthesizes this early. |
| 6–9  | Synthesizes **if** a clear repeating pattern is visible, else asks again. |
| 10+  | Forces a synthesis. |

The client doesn't enforce any of this — just send replies and branch on the
`type` field. `turn` is returned on every response so you can show progress.

---

## `POST /session/start`

Creates a session and returns the AI's opening question.

**Request**
```json
{ "user_id": "demo-user" }
```

**Response `200`**
```json
{
  "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f",
  "message": "Think of a recent moment where you reacted in a way that surprised you. What happened?",
  "turn": 1
}
```

Store `session_id` — every subsequent call needs it. Render `message` as the
first assistant bubble.

### ⚠️ `user_id` must already exist in prod (onboarding ordering)

In production (Supabase active), `sessions.user_id` is a **`NOT NULL` foreign key
to `public.users(user_id)`** (and so is `patterns.user_id`). So the `user_id` you
pass to `/session/start` **must already be a real row in `public.users`** —
otherwise the insert fails the FK constraint and the API returns **`500`** (it
does *not* silently fall back to questions; that fallback would be the app's own
local handling of the error). In local/in-memory mode any string works.

This matters because the account is normally created at the **end** of
onboarding — so if you run the Mirror chat *during* onboarding, the user doesn't
exist yet.

**Required pattern: create the identity up front, upgrade it later.**

1. At the **start** of onboarding, create the user so a `public.users` row
   exists — e.g. Supabase **anonymous sign-in**
   (`supabase.auth.signInAnonymously()`), ensuring a matching `public.users` row
   is created (via your signup trigger, or an explicit insert). Use that user's
   **UUID** as `user_id` for `/session/start`.
2. Run the Mirror chat normally — sessions/messages/patterns now persist against
   that UUID.
3. At the **end** of onboarding, **upgrade** the anonymous user into a permanent
   account (attach email/password). The **UUID is stable**, so every session and
   pattern created during onboarding stays linked to the now-permanent account —
   no migration/relinking needed.

> Plan a periodic cleanup for anonymous users who abandon onboarding (the
> `on delete cascade` FKs will drop their sessions/messages/patterns with them).

> **The Growth Area insight needs none of this** — `POST /insights/growth-area`
> is stateless and has no user FK, so onboarding can call it before any account
> exists. This requirement applies only to the session-chat endpoints.

---

## `POST /session/reply`

Sends the user's message and returns the next assistant turn. This is the core
loop — **branch on `type`**.

**Request**
```json
{
  "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f",
  "user_message": "I went quiet the moment my partner asked what was wrong."
}
```

**Response — still asking (`type: "question"`)**
```json
{
  "type": "question",
  "content": "What did going quiet protect you from in that moment?",
  "turn": 3,
  "session_complete": false
}
```
Here `content` is a **plain string** — render it as the next assistant bubble.

**Response — pattern found (`type: "synthesis"`)**
```json
{
  "type": "synthesis",
  "content": {
    "pattern_name": "Proximity Withdrawal Loop",
    "pattern_summary": "When closeness asks something of you, you go quiet to stay safe — which creates the distance you feared.",
    "trigger": "A partner reaching toward you emotionally.",
    "response": "You withdraw and go silent.",
    "insight": "The silence isn't absence — it's a guard you post when intimacy feels like exposure.",
    "next_step": "Next time you feel the pull to go quiet, you could name the urge out loud instead."
  },
  "turn": 7,
  "session_complete": true
}
```
Here `content` is a **`PatternObject`** (see [Data models](#data-models)). When
`session_complete` is `true`, stop the loop and show the synthesis card.

> **Key client rule:** `content` is a string when `type == "question"` and an
> object when `type == "synthesis"`. Switch on `type` before parsing `content`.

**Idempotency / edge cases**
- Replying to an already-complete session returns the **same synthesis** again
  (`session_complete: true`).
- If the session ended *without* a pattern, reply returns `409`.

---

## `GET /session/{session_id}`

Fetch the full state — use it to rehydrate a conversation when the user reopens
the app.

**Response `200`**
```json
{
  "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f",
  "user_id": "demo-user",
  "status": "complete",
  "turn": 7,
  "messages": [
    { "role": "assistant", "content": "Think of a recent moment...", "timestamp": "2026-06-27T19:51:31.423000+00:00" },
    { "role": "user", "content": "I went quiet...", "timestamp": "2026-06-27T19:52:02.110000+00:00" }
  ],
  "pattern": { "pattern_name": "Proximity Withdrawal Loop", "pattern_summary": "...", "trigger": "...", "response": "...", "insight": "...", "next_step": "..." }
}
```
`status` is `"active"` or `"complete"`. `pattern` is `null` until a synthesis
exists. Returns `404` if the id is unknown.

---

## `POST /session/end`

Ends the session. If `turn >= 6` and a pattern can be honestly named, it forces a
synthesis; otherwise it closes the session without fabricating one.

**Request**
```json
{ "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f" }
```

**Response `200` — synthesized**
```json
{
  "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f",
  "status": "complete",
  "pattern": { "pattern_name": "...", "pattern_summary": "...", "trigger": "...", "response": "...", "insight": "...", "next_step": "..." },
  "message": null
}
```

**Response `200` — ended without a pattern**
```json
{
  "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f",
  "status": "complete",
  "pattern": null,
  "message": "Session ended before enough signal accumulated to surface a pattern."
}
```
When `pattern` is `null`, show `message` instead of a synthesis card.

---

## `GET /patterns/{user_id}`

All confirmed patterns for a user, newest first — feeds a "your patterns"
history list.

**Response `200`**
```json
{
  "patterns": [
    { "pattern_name": "Proximity Withdrawal Loop", "pattern_summary": "...", "trigger": "...", "response": "...", "insight": "...", "next_step": "..." }
  ]
}
```
`patterns` is `[]` when the user has none yet.

---

# 2. The Growth Area insight

`POST /insights/growth-area`

Scores six personal-growth dimensions (`0–100`) from the user's profile +
progress data. This is what the **Growth Area** card on the Insights screen
renders (six progress rings or a radar chart).

### Key properties for the client

- **Stateless** — no `session_id`, no DB writes. Call it whenever the Insights
  screen needs fresh scores.
- **Always succeeds with `200`** — if the scoring model is unavailable or returns
  bad output, the server falls back to a deterministic heuristic. The card never
  fails to render.
- **Every input field is optional** — send what you have. Thin data returns
  moderate/base scores rather than overconfident ones.

### Request

```json
{
  "goals": ["thoughts that loop", "work stress"],
  "stress_frequency": "a few times a week",
  "happiness_level": "catching myself before i react",
  "ai_onboarding_responses": [
    { "question": "What brings you here?", "answer": "I feel anxious and stuck, but I'm noticing the pattern now." }
  ],
  "progress_metrics": {
    "loops_identified": 3,
    "top_loops": ["overthinking"],
    "trigger_clarity_pct": 70,
    "reaction_autopilot_pct": 55,
    "interrupt_rate_pct": 60,
    "ladder_stage": "mapper"
  },
  "user_memory": {
    "entries_count": 12,
    "active_loops": ["overthinking", "avoidance"],
    "emotion_distribution": { "anxious": 0.6, "calm": 0.4 },
    "narrative_summary": "Often stressed about deadlines at work; learning to pause."
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

**Field reference**

| Field | Type | Source table | Notes |
|-------|------|--------------|-------|
| `goals` | `string[]` | `users.goals` | What the user wants to work on. |
| `stress_frequency` | `string` | `users.stress_frequency` | e.g. `"almost every day"`. |
| `happiness_level` | `string` | `users.happiness_level` | A phrase, not a number. |
| `ai_onboarding_responses` | `{question, answer}[]` | `users.ai_onboarding_responses` | Capped server-side to 8 items; `question`≤280, `answer`≤700 chars. |
| `progress_metrics` | object | `progress_metrics` | See below; all default to `0`/empty. |
| `user_memory` | object | `user_memory` | `narrative_summary`≤600 chars; `active_loops`≤5 items (server-trimmed). |
| `fallback_scores` | `{id, value}[]` | computed client- or server-side | Optional anchor for thin data. **Omit it** and the server computes its own. |

`progress_metrics` fields: `loops_identified` (int), `top_loops` (string[]),
`trigger_clarity_pct` / `reaction_autopilot_pct` / `interrupt_rate_pct` (ints
`0–100`), `ladder_stage` (`"spotter" | "namer" | "mapper" | "interruptor" |
"rewriter" | "stabilizer"`, or `""` if none yet).

### Response `200`

```json
{
  "areas": [
    { "id": "mental_health", "value": 48 },
    { "id": "growth_mindset", "value": 58 },
    { "id": "relationships", "value": 46 },
    { "id": "personal_development", "value": 48 },
    { "id": "self_awareness", "value": 70 },
    { "id": "stress_management", "value": 66 }
  ],
  "source": "finetuned"
}
```

**Guarantees (the strict output contract):**
- `areas` always contains **all six ids, exactly once, in this fixed order**.
- Each `value` is a **whole number `0–100`**.
- `source` is `"finetuned"` (model scored it) or `"profile"` (heuristic fallback).
  **The UI should ignore `source`** — it's for observability/analytics only.

### id → UI label mapping

| `id` | Card label (grid position) | Icon |
|------|---------------------------|------|
| `mental_health` | Mental Health (top-left) | 😌 |
| `growth_mindset` | Growth Mindset (top-mid) | 🧠 |
| `relationships` | Relationships (top-right) | ❤️ |
| `personal_development` | Personal Dev (bottom-left) | 🌱 |
| `self_awareness` | Self-awareness (bottom-mid) | 🧘 |
| `stress_management` | Stress Mgt (bottom-right) | 💆 |

> The existing `growth_area_service.dart` already consumes `{ "areas": [...] }`
> and clamps to `[0,100]` — no client changes are required to adopt this endpoint.

---

# 3. End-to-end example (curl)

```bash
BASE=http://localhost:8000

# 1) Start mirror chat
START=$(curl -s -X POST $BASE/mirror/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo-user"}')
echo "$START" | python3 -m json.tool
SID=$(echo "$START" | python3 -c "import sys,json;print(json.load(sys.stdin)['session_id'])")

# 2) Reply (repeat until type == "synthesis")
curl -s -X POST $BASE/mirror/chat \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SID\",\"message\":\"I went quiet when my partner asked what was wrong.\"}"

# 3) (optional) Force-close the chat
curl -s -X POST $BASE/mirror/chat/end \
  -H "Content-Type: application/json" -d "{\"session_id\":\"$SID\"}"

# 4) List the user's confirmed patterns
curl -s $BASE/patterns/demo-user

# 5) Score the Growth Area insight (independent of the chat)
curl -s -X POST $BASE/insights/growth-area \
  -H "Content-Type: application/json" \
  -d '{"goals":["thoughts that loop"],"stress_frequency":"a few times a week"}'
```

---

# 4. Flutter integration sketch (`package:http`)

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;

const baseUrl = 'http://localhost:8000'; // use your deployed URL in prod

// ── Mirror chat — start ───────────────────────────────────────────
Future<Map<String, dynamic>> startMirrorChat(String userId) async {
  final res = await http.post(
    Uri.parse('$baseUrl/mirror/chat'),
    headers: {'Content-Type': 'application/json'},
    body: jsonEncode({'user_id': userId}),
  );
  return jsonDecode(res.body) as Map<String, dynamic>;
  // → session_id, type:"opening", content (string), turn, session_complete
}

// ── Mirror chat — send next message; branch on `type` ─────────────
Future<void> sendMirrorMessage(String sessionId, String message) async {
  final res = await http.post(
    Uri.parse('$baseUrl/mirror/chat'),
    headers: {'Content-Type': 'application/json'},
    body: jsonEncode({'session_id': sessionId, 'message': message}),
  );
  final data = jsonDecode(res.body) as Map<String, dynamic>;

  if (data['type'] == 'synthesis') {
    final pattern = data['content'] as Map<String, dynamic>; // PatternObject
    // → show synthesis card; session is complete
  } else {
    final question = data['content'] as String;
    // → render next assistant bubble (type is "question")
  }
}

// ── Growth Area insight (stateless; safe to call anytime) ─────────
Future<List<Map<String, dynamic>>> fetchGrowthArea(Map<String, dynamic> profile) async {
  final res = await http.post(
    Uri.parse('$baseUrl/insights/growth-area'),
    headers: {'Content-Type': 'application/json'},
    body: jsonEncode(profile), // any subset of the documented fields
  );
  final data = jsonDecode(res.body) as Map<String, dynamic>;
  return (data['areas'] as List).cast<Map<String, dynamic>>(); // 6 × {id, value}
}
```

---

# Data models

### `PatternObject`
Returned by `/session/reply` (on synthesis), `/session/{id}`, `/session/end`, and `/patterns/{user_id}`.

| Field | Type | Meaning |
|-------|------|---------|
| `pattern_name` | string | Short, evocative, non-clinical name. |
| `pattern_summary` | string | 2–3 sentences naming the recurring chain. |
| `trigger` | string | The situation that reliably activates it. |
| `response` | string | What the user characteristically does. |
| `insight` | string | One precise observation that makes it visible. |
| `next_step` | string | One small option the user *could* choose. |

### `Message`
| Field | Type | Meaning |
|-------|------|---------|
| `role` | string | `"assistant"` or `"user"`. |
| `content` | string | The text. |
| `timestamp` | string | ISO-8601. |

### `AreaScore`
| Field | Type | Meaning |
|-------|------|---------|
| `id` | string | One of the six canonical area ids. |
| `value` | int | `0–100`. |
