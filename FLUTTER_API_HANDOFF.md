# Patternverse API — Mirror Chat (Flutter Handoff)

> **For the AI code editor wiring up the Chat tab.**
> Wire the Mirror conversation against this REST API. JSON only. No auth headers today. CORS is open.

---

## Server

| | |
|---|---|
| **Local dev** | `http://localhost:8001` (or whatever port uvicorn uses) |
| **Swagger UI** | `GET /docs` — try endpoints live |
| **Health check** | `GET /health` |

Set the base URL in one place (env/config). Use your deployed URL in production.

---

## Endpoints (Chat tab only)

| Action | Method | Path |
|--------|--------|------|
| Start chat **or** send next message | `POST` | `/mirror/chat` |
| Reload conversation | `GET` | `/mirror/chat/{session_id}` |
| End chat early | `POST` | `/mirror/chat/end` |

Use **`POST /mirror/chat`** for both starting and continuing — one endpoint for the whole chat screen.

---

## How it works

Turn-based reflective conversation. The AI asks one question at a time. When a
genuine repeating pattern is visible, it returns a structured **synthesis**
(`PatternObject`). Branch on `type` in the response — do not hard-code turn logic
on the client.

```
POST /mirror/chat  { "user_id": "..." }
        → type:"opening", session_id, content (string)

POST /mirror/chat  { "session_id": "...", "message": "..." }
        → type:"question"  (keep going)
        → type:"synthesis" (session_complete: true — show pattern card)

Optional: POST /mirror/chat/end  { "session_id": "..." }
GET  /mirror/chat/{session_id}   (reopen app / restore thread)
```

---

## Start chat

**Request**
```json
{ "user_id": "550e8400-e29b-41d4-a716-446655440000" }
```

Send `user_id` only. Do **not** send `session_id` or `message`.

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

Persist `session_id` locally. Render `content` as the first assistant bubble.

---

## Send message

**Request**
```json
{
  "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f",
  "message": "I went quiet when my partner asked what was wrong."
}
```

**Response — question (`type: "question"`)**
```json
{
  "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f",
  "type": "question",
  "content": "What did going quiet protect you from in that moment?",
  "turn": 3,
  "session_complete": false
}
```

`content` is a **string** → render as assistant bubble.

**Response — synthesis (`type: "synthesis"`)**
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

`content` is a **`PatternObject`** → show synthesis UI. Stop sending messages.

---

## Client parsing rule (critical)

```dart
switch (data['type'] as String) {
  case 'opening':
  case 'question':
    final text = data['content'] as String;
    break;
  case 'synthesis':
    final pattern = data['content'] as Map<String, dynamic>;
    break;
}
```

- `content` is a **string** when `type` is `"opening"` or `"question"`.
- `content` is an **object** when `type` is `"synthesis"`.
- Always switch on `type` before parsing `content`.

---

## Reload chat state

**`GET /mirror/chat/{session_id}`**

```json
{
  "session_id": "...",
  "user_id": "...",
  "status": "active",
  "turn": 3,
  "messages": [
    { "role": "assistant", "content": "...", "timestamp": "2026-06-27T19:51:31.423000+00:00" },
    { "role": "user", "content": "...", "timestamp": "2026-06-27T19:52:02.110000+00:00" }
  ],
  "pattern": null
}
```

- `status`: `"active"` | `"complete"`
- `pattern`: non-null after synthesis
- Returns `404` if session id is unknown

---

## End chat early

**`POST /mirror/chat/end`**
```json
{ "session_id": "f1c3a0a2-7d3a-4c2e-9b1e-0a1b2c3d4e5f" }
```

**Response `200` — synthesized**
```json
{
  "session_id": "...",
  "status": "complete",
  "pattern": {
    "pattern_name": "...",
    "pattern_summary": "...",
    "trigger": "...",
    "response": "...",
    "insight": "...",
    "next_step": "..."
  },
  "message": null
}
```

**Response `200` — no pattern**
```json
{
  "session_id": "...",
  "status": "complete",
  "pattern": null,
  "message": "Session ended before enough signal accumulated to surface a pattern."
}
```

If `pattern` is `null`, show `message` instead of a synthesis card.

---

## Errors

All errors: `{ "detail": "..." }`

| Status | Meaning | Client action |
|--------|---------|---------------|
| `404` | Unknown `session_id` | Start a new chat |
| `409` | Session ended without a pattern | Start a new chat |
| `422` | Bad payload (e.g. missing `message` on reply) | Fix request |
| `503` | Model backend down | Show retry |
| `500` | DB error (often missing `public.users` row in prod) | Show error; see prod note |

Replying to a **completed** session that has a pattern returns the same synthesis again (`200`, `session_complete: true`).

---

## Prod: user must exist before starting chat

In production, `sessions.user_id` is a foreign key to `public.users(user_id)`.
Starting chat with a UUID that has no `public.users` row → **`500`**.

If chat runs **during** onboarding (before signup finishes), create the identity
**up front**:

1. **Start of onboarding:** `supabase.auth.signInAnonymously()` + ensure a
   `public.users` row exists (trigger or explicit insert).
2. Use that **UUID** as `user_id` in `POST /mirror/chat`.
3. **End of onboarding:** upgrade anonymous → permanent (email/password). The
   UUID stays the same — sessions remain linked.

Confirm your project creates `public.users` on auth signup. Without that trigger,
insert the row manually after anonymous sign-in.

In local dev (in-memory storage), any string works for `user_id`.

---

## Dart service sketch

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;

class MirrorChatApi {
  MirrorChatApi({required this.baseUrl});
  final String baseUrl;

  Future<Map<String, dynamic>> startChat(String userId) async {
    final res = await http.post(
      Uri.parse('$baseUrl/mirror/chat'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'user_id': userId}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
    // → session_id, type:"opening", content (string), turn, session_complete
  }

  Future<Map<String, dynamic>> sendMessage(
    String sessionId,
    String message,
  ) async {
    final res = await http.post(
      Uri.parse('$baseUrl/mirror/chat'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'session_id': sessionId, 'message': message}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
    // → branch on type: "question" | "synthesis"
  }

  Future<Map<String, dynamic>> getChat(String sessionId) async {
    final res = await http.get(Uri.parse('$baseUrl/mirror/chat/$sessionId'));
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> endChat(String sessionId) async {
    final res = await http.post(
      Uri.parse('$baseUrl/mirror/chat/end'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'session_id': sessionId}),
    );
    _throwIfError(res);
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  void _throwIfError(http.Response res) {
    if (res.statusCode >= 400) {
      throw Exception('API ${res.statusCode}: ${res.body}');
    }
  }
}
```

---

## Quick curl test

```bash
BASE=http://localhost:8001

# Start
curl -s -X POST $BASE/mirror/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo-user"}' | python3 -m json.tool

# Reply (paste session_id from above)
curl -s -X POST $BASE/mirror/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<SESSION_ID>","message":"I went quiet when my partner asked what was wrong."}' \
  | python3 -m json.tool
```

---

## Data models

### `PatternObject` (synthesis)
| Field | Type |
|-------|------|
| `pattern_name` | string |
| `pattern_summary` | string |
| `trigger` | string |
| `response` | string |
| `insight` | string |
| `next_step` | string |

### `Message` (in GET session detail)
| Field | Type |
|-------|------|
| `role` | `"assistant"` \| `"user"` |
| `content` | string |
| `timestamp` | ISO-8601 string |
