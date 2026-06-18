-- ============================================================================
-- Patternverse MIS API — conversational session tables
-- Tables: sessions, messages, patterns  (+ Row Level Security)
--
-- APPLIED to project cvcjzqqhyiqcaedaxskn ("Patternverse") on 2026-06-18 as
-- migration `create_mis_api_session_tables`.
--
-- This is ADDITIVE: it introduces three NEW tables for the Mistral conversation
-- API and does not touch the project's existing MIS schema (users, entries,
-- loops, mirror_responses, user_memory, ...). `user_id` is a uuid referencing
-- public.users(user_id), and RLS follows the project convention auth.uid() = user_id.
--
-- Idempotent: safe to re-run (create ... if not exists + drop policy if exists).
-- ============================================================================

create extension if not exists pgcrypto;  -- gen_random_uuid()

-- ── sessions ────────────────────────────────────────────────────────────────
create table if not exists public.sessions (
    session_id  uuid        primary key default gen_random_uuid(),
    user_id     uuid        not null references public.users (user_id) on delete cascade,
    created_at  timestamptz not null default now(),
    status      text        not null default 'active'
                            check (status in ('active', 'complete')),
    turn        integer     not null default 0
);
create index if not exists sessions_user_id_idx on public.sessions (user_id);

-- ── messages ────────────────────────────────────────────────────────────────
create table if not exists public.messages (
    id          bigint generated always as identity primary key,
    session_id  uuid        not null
                            references public.sessions (session_id) on delete cascade,
    role        text        not null
                            check (role in ('system', 'assistant', 'user')),
    content     text        not null,
    "timestamp" timestamptz not null default now()
);
create index if not exists messages_session_id_idx
    on public.messages (session_id, "timestamp");

-- ── patterns ────────────────────────────────────────────────────────────────
create table if not exists public.patterns (
    id              uuid        primary key default gen_random_uuid(),
    session_id      uuid        not null
                                references public.sessions (session_id) on delete cascade,
    user_id         uuid        not null
                                references public.users (user_id) on delete cascade,
    pattern_name    text        not null,
    pattern_summary text        not null,
    trigger         text        not null,
    response        text        not null,
    insight         text        not null,
    next_step       text        not null,
    created_at      timestamptz not null default now()
);
create index if not exists patterns_user_id_idx on public.patterns (user_id);
create index if not exists patterns_session_idx on public.patterns (session_id);

-- ============================================================================
-- Row Level Security — protects direct client (anon-key) access. The API server
-- itself uses the SERVICE-ROLE key (SUPABASE_SERVICE_KEY), which bypasses RLS.
-- ============================================================================
alter table public.sessions enable row level security;
alter table public.messages enable row level security;
alter table public.patterns enable row level security;

drop policy if exists "sessions_select_own" on public.sessions;
drop policy if exists "sessions_insert_own" on public.sessions;
drop policy if exists "sessions_update_own" on public.sessions;
drop policy if exists "sessions_delete_own" on public.sessions;
create policy "sessions_select_own" on public.sessions for select using (auth.uid() = user_id);
create policy "sessions_insert_own" on public.sessions for insert with check (auth.uid() = user_id);
create policy "sessions_update_own" on public.sessions for update using (auth.uid() = user_id);
create policy "sessions_delete_own" on public.sessions for delete using (auth.uid() = user_id);

drop policy if exists "messages_select_own" on public.messages;
drop policy if exists "messages_insert_own" on public.messages;
create policy "messages_select_own" on public.messages for select using (
    exists (select 1 from public.sessions s
            where s.session_id = messages.session_id and s.user_id = auth.uid()));
create policy "messages_insert_own" on public.messages for insert with check (
    exists (select 1 from public.sessions s
            where s.session_id = messages.session_id and s.user_id = auth.uid()));

drop policy if exists "patterns_select_own" on public.patterns;
drop policy if exists "patterns_insert_own" on public.patterns;
drop policy if exists "patterns_delete_own" on public.patterns;
create policy "patterns_select_own" on public.patterns for select using (auth.uid() = user_id);
create policy "patterns_insert_own" on public.patterns for insert with check (auth.uid() = user_id);
create policy "patterns_delete_own" on public.patterns for delete using (auth.uid() = user_id);
