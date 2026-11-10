-- App tables. LangGraph's own checkpoint tables are created separately by
-- scripts/setup_checkpointer.py (AsyncPostgresSaver.setup()).
-- Names keep the adk_ prefix so the existing KB data can be copied over as-is.

create extension if not exists vector;

create table if not exists adk_kb_docs (
    id serial primary key,
    agent_slug text not null,
    filename text not null,
    content text not null,
    active boolean not null default true,
    uploaded_at timestamptz default now()
);

create table if not exists adk_kb_chunks (
    id serial primary key,
    doc_id integer not null references adk_kb_docs(id) on delete cascade,
    agent_slug text not null,
    chunk_text text not null,
    embedding vector(512)
);
create index if not exists adk_kb_chunks_agent_idx on adk_kb_chunks (agent_slug);

create table if not exists usage_logs (
    id serial primary key,
    agent_slug text,
    session_id text,
    model text,
    input_tokens integer,
    output_tokens integer,
    created_at timestamptz default now()
);

create table if not exists agent_configs (
    agent_slug text primary key,
    system_prompt text,
    model text,
    updated_at timestamptz not null default now()
);

-- Shared fixed-window rate limiter (works across any number of instances).
create table if not exists rate_limits (
    key text not null,
    bucket bigint not null,
    count integer not null default 0,
    primary key (key, bucket)
);

-- Booking idempotency: one row per (member, class occurrence). A repeated
-- "yes", a retry, or two instances racing can never double-book.
create table if not exists booking_attempts (
    member_id bigint not null,
    calendar_schedule_id bigint not null,
    status text not null default 'pending',   -- pending | booked
    created_at timestamptz not null default now(),
    primary key (member_id, calendar_schedule_id)
);

-- These tables are only ever touched by the backend with the database
-- role. Lock them against the public PostgREST API.
alter table adk_kb_docs enable row level security;
alter table adk_kb_chunks enable row level security;
alter table usage_logs enable row level security;
alter table agent_configs enable row level security;
alter table rate_limits enable row level security;
alter table booking_attempts enable row level security;
