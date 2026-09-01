-- Run this once in your Supabase project's SQL Editor (SQL Editor -> New query -> paste -> Run).
-- One table covers both extraction kinds: `meta` holds the flat fields
-- (patient name / age-sex / reported-on for blood tests; utr / amount /
-- payer / payee / status for payments), `rows` holds the per-analyte rows
-- for blood tests only (empty array for payments).

create table extractions (
    id bigint generated always as identity primary key,
    created_at timestamptz not null default now(),
    kind text not null check (kind in ('payment', 'blood_test', 'test')),
    filename text not null,
    meta jsonb not null default '{}'::jsonb,
    rows jsonb not null default '[]'::jsonb
);

create index extractions_kind_idx on extractions (kind);
create index extractions_created_at_idx on extractions (created_at desc);

-- No RLS policy is added on purpose: only the backend writes here, using the
-- service_role key, which bypasses RLS entirely. Nothing reads this table
-- from the browser, so there's no anon-key access to guard against.
