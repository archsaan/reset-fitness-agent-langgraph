# Reset Fitness agents - LangGraph

First pass: **tribe-app (Riley)** only, ported from the ADK project.
Postgres = Supabase. Host = any container platform (Cloud Run, Render, Fly).

## Layout

    app/agents/tribe.py     the graph: prepare -> agent -> tools | confirm_booking
    app/tools/              async ProfitConnect tools (shared client, retry, idempotent booking)
    app/auth.py             Supabase JWT -> member_id / admin (identity never from the body)
    app/rate_limit.py       Postgres-backed, shared across instances
    app/kb.py, agent_config.py   RAG + admin-editable persona (30s cache)
    supabase/migrations/    app tables; checkpoint tables via scripts/setup_checkpointer.py

## Setup

1. Supabase: create a project, then run `supabase/migrations/0001_init.sql`
   (SQL editor or `supabase db push`).
2. `cp .env.example .env` and fill it in. `DATABASE_URL` is the **transaction
   pooler** (6543); `DIRECT_DATABASE_URL` is session mode (5432).
3. `pip install -r requirements-dev.txt`
4. `python -m scripts.setup_checkpointer` (once)
5. Give each member's Supabase user `app_metadata.member_id` (numeric
   ProfitConnect id) and staff `app_metadata.role = "admin"`. Use
   **app_metadata**, not user_metadata - users can edit the latter.
6. `uvicorn app.main:app --reload`

## API

`POST /chat` with `Authorization: Bearer <supabase access token>`

    {"message": "book yoga tomorrow", "session_id": "optional"}
    -> {"reply": "...", "session_id": "...", "awaiting_confirmation": false}

When `awaiting_confirmation` is true, the reply is the confirmation question;
the member's next `/chat` message (same session_id) is the answer.

## Tests

`pytest` - runs the real graph with a scripted model, no DB / network / keys.

## Not ported yet

pfc, riley_whatsapp, member_goal_setter, weekly goal batch, admin routes
(KB upload, config, usage, session stats), SSE streaming. `app/kb.py` and
`app/agent_config.py` already hold the data layer the admin routes need.
