# Patternverse — Mirror Intelligence System (MIS) API

A production-ready **FastAPI** backend for Patternverse: a self-awareness and
behavioral-pattern-recognition system. The AI asks one precise, emotionally
intelligent question at a time, listens, and — once a pattern is genuinely
visible — returns a structured **synthesis** of the loop the user keeps
repeating.

> **Governing doctrine:** *Mirror stays mirror.* No advice. No flattery. No
> diagnosis. No premature conclusions. (See
> `../Patternverse_Mirror_Intelligence_System_v1.0.pdf`.)

The LLM is swappable — **Ollama** locally, **Hugging Face Inference API** in
production — controlled entirely by a single environment variable.

---

## Architecture

```
patternverse-api/
├── main.py                     # FastAPI app, CORS, routers, /health, error handling
├── config.py                   # Settings (reads .env / environment)
├── requirements.txt
├── .env.example
├── services/
│   ├── model_service.py        # Ollama + HF swappable logic (one interface)
│   ├── conversation.py         # System prompt, turn flow, synthesis parsing
│   └── supabase_service.py     # All DB ops (Supabase + in-memory fallback)
├── routes/
│   ├── session.py              # /session/start, /reply, /{id}, /end
│   └── patterns.py             # /patterns/{user_id}
├── models/
│   └── schemas.py              # All Pydantic models
└── supabase/
    └── migrations/0001_init.sql  # sessions, messages, patterns + RLS
```

### Conversation flow
| Turn    | Behavior                                                        |
|---------|-----------------------------------------------------------------|
| 1–5     | Questions only — never synthesize this early.                   |
| 6–9     | Synthesize **if** a clear pattern emerges, else keep asking.    |
| 10+     | Force synthesis (retry once on invalid JSON).                   |

Every model response after turn 6 is JSON-parsed: a valid `PatternObject` →
`synthesis` (session marked complete); otherwise it's treated as the next
`question`.

---

## API endpoints

| Method | Path                    | Purpose                                  |
|--------|-------------------------|------------------------------------------|
| POST   | `/session/start`        | Create a session, return opening question|
| POST   | `/session/reply`        | Send a user message, get question/synthesis |
| GET    | `/session/{session_id}` | Full session state (messages + pattern)  |
| POST   | `/session/end`          | Force synthesis if `turn >= 6`, complete |
| GET    | `/patterns/{user_id}`   | All confirmed patterns for a user        |
| GET    | `/health`               | Status + active environment + model      |

Interactive docs (Swagger UI) are served at **`/docs`**.

---

## 1. Local setup with Ollama (step by step)

1. **Install Ollama** → <https://ollama.com/download>
2. **Start the server** (keep this running in its own terminal):
   ```bash
   ollama serve
   ```
3. **Pull the model:**
   ```bash
   ollama pull mistral
   ```
4. **Create a virtualenv and install deps:**
   ```bash
   cd patternverse-api
   python3 -m venv .venv
   source .venv/bin/activate         # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
5. **Configure environment:**
   ```bash
   cp .env.example .env
   # APP_ENV=development is the default — nothing else needed to run with Ollama.
   ```
   > Leaving `SUPABASE_URL` blank makes the API use a built-in **in-memory
   > store** so you can run end-to-end with nothing but Ollama. Data resets
   > when the process stops — set up Supabase (below) for persistence.
6. **Run the API:**
   ```bash
   uvicorn main:app --reload --port 8000
   ```
7. **Smoke test:**
   ```bash
   curl http://localhost:8000/health
   # → {"status":"ok","environment":"development","model":"ollama"}

   curl -X POST http://localhost:8000/session/start \
        -H "Content-Type: application/json" \
        -d '{"user_id":"demo-user"}'
   ```
   Then feed the returned `session_id` into `/session/reply`.

---

## 2. How to get a Hugging Face API token

1. Create / sign in to an account at <https://huggingface.co>.
2. Go to **Settings → Access Tokens** (<https://huggingface.co/settings/tokens>).
3. **New token** → role **Read** → copy it.
4. Put it in `.env`:
   ```
   HF_API_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxx
   HF_MODEL_URL=https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3
   ```
5. Accept the model terms once on its page if prompted:
   <https://huggingface.co/mistralai/Mistral-7B-Instruct-v0.3>.

---

## 3. Supabase setup

> **Already provisioned.** The `sessions`, `messages`, and `patterns` tables
> (with RLS) have been applied to project **`cvcjzqqhyiqcaedaxskn` ("Patternverse")**.
> They were added *alongside* the project's existing MIS schema (`users`,
> `entries`, `loops`, `mirror_responses`, …) — `user_id` is a `uuid` that
> references `public.users(user_id)`, and RLS follows the project convention
> `auth.uid() = user_id`. `SUPABASE_URL` and `SUPABASE_ANON_KEY` are already
> filled into `.env`.

**To activate Supabase you only need the service-role key:**

1. Dashboard → **Project Settings → API → service_role** (reveal & copy):
   <https://supabase.com/dashboard/project/cvcjzqqhyiqcaedaxskn/settings/api>
2. Paste it into `.env` as `SUPABASE_SERVICE_KEY=...` (never expose it to a browser).
3. Restart the API. It switches from in-memory to Supabase automatically — watch
   the startup log for `storage=supabase`.

To re-create these tables on a *different* project, run
`supabase/migrations/0001_init.sql` in that project's SQL editor.

**Why the service-role key is required (not just recommended):** RLS is enabled
and the policies are `auth.uid() = user_id`. This backend is a trusted server
with no per-request user JWT, so `auth.uid()` is null and the **anon key is
blocked** for every read/write. The service-role key bypasses RLS — the standard
trusted-server pattern. (The API guards against this: with only the anon key it
stays on the in-memory store rather than activating a broken connection.)

**Testing note — the `users` foreign key:** because `sessions.user_id` references
`public.users(user_id)`, `POST /session/start` requires a **real `users.user_id`
(uuid)**. Pass an existing user's id, or drop the FK if you want to test with
synthetic ids (`alter table public.sessions drop constraint sessions_user_id_fkey;`
and the same for `patterns`).

---

## 4. Deploy to Render.com

1. Push this folder to a Git repository.
2. On Render: **New → Web Service** → connect the repo.
3. Settings:
   - **Runtime:** Python 3
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:**
     ```
     uvicorn main:app --host 0.0.0.0 --port $PORT
     ```
4. **Environment** tab → add:
   ```
   APP_ENV=production
   SUPABASE_URL=...
   SUPABASE_SERVICE_KEY=...        # (or SUPABASE_ANON_KEY)
   HF_API_TOKEN=...
   HF_MODEL_URL=https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3
   ```
5. Deploy. Verify `GET /health` returns
   `{"status":"ok","environment":"production","model":"huggingface"}`.

---

## 5. Switching from dev to production

Change **one** variable:

```diff
- APP_ENV=development   # Ollama (local)
+ APP_ENV=production    # Hugging Face Inference API
```

No code changes. `model_service.py` reads `APP_ENV` and routes accordingly;
the rest of the app neither knows nor cares which backend is active.

---

## Error handling

| Situation                  | Response                                            |
|----------------------------|-----------------------------------------------------|
| Session not found          | `404`                                               |
| Model backend unavailable  | `503` with a clear message                          |
| Invalid JSON from model    | retried once, then falls back to a generic question |
| Storage / DB error         | `500` with logged detail                            |
