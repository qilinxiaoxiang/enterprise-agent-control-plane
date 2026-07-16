CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS control;
CREATE SCHEMA IF NOT EXISTS langgraph_checkpoints;

CREATE TABLE IF NOT EXISTS cases (
  id UUID PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  request JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  is_public_demo BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cases_tenant_created_idx ON cases (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS runs (
  id UUID PRIMARY KEY,
  case_id UUID NOT NULL REFERENCES cases(id),
  tenant_id TEXT NOT NULL,
  status TEXT NOT NULL,
  state JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_public_demo BOOLEAN NOT NULL DEFAULT FALSE,
  replay_of UUID REFERENCES runs(id),
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS runs_tenant_started_idx ON runs (tenant_id, started_at DESC);

CREATE TABLE IF NOT EXISTS source_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES runs(id),
  tenant_id TEXT NOT NULL,
  source TEXT NOT NULL,
  record_id TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ,
  authoritative BOOLEAN NOT NULL,
  facts JSONB NOT NULL,
  UNIQUE (run_id, source, record_id)
);

CREATE TABLE IF NOT EXISTS policy_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  policy_id TEXT NOT NULL,
  version TEXT NOT NULL,
  section TEXT NOT NULL,
  content TEXT NOT NULL,
  effective_at TIMESTAMPTZ NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  embedding vector(768),
  UNIQUE (policy_id, version, section)
);
CREATE INDEX IF NOT EXISTS policy_chunks_embedding_hnsw
  ON policy_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS run_events (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES runs(id),
  event_type TEXT NOT NULL,
  node TEXT NOT NULL,
  message TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS run_events_stream_idx ON run_events (run_id, created_at);

CREATE TABLE IF NOT EXISTS tool_invocations (
  id UUID PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES runs(id),
  tool_name TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL,
  effect_id TEXT,
  response JSONB NOT NULL DEFAULT '{}'::jsonb,
  attempt_count INTEGER NOT NULL DEFAULT 1,
  committed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tool_name, idempotency_key)
);

CREATE TABLE IF NOT EXISTS approvals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES runs(id),
  approved BOOLEAN NOT NULL,
  actor_id TEXT NOT NULL,
  actor_role TEXT NOT NULL,
  comment TEXT NOT NULL DEFAULT '',
  action_hash TEXT NOT NULL,
  decided_at TIMESTAMPTZ NOT NULL,
  UNIQUE (run_id, action_hash)
);

CREATE TABLE IF NOT EXISTS effect_ledger (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  effect_id TEXT,
  request_hash TEXT NOT NULL,
  response JSONB NOT NULL DEFAULT '{}'::jsonb,
  committed_at TIMESTAMPTZ,
  compensated_by UUID REFERENCES effect_ledger(id)
);
CREATE INDEX IF NOT EXISTS effect_ledger_tenant_idx ON effect_ledger (tenant_id, committed_at DESC);

CREATE TABLE IF NOT EXISTS webhook_receipts (
  event_id TEXT PRIMARY KEY,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval_runs (
  id TEXT PRIMARY KEY,
  suite_version TEXT NOT NULL,
  model_provider TEXT NOT NULL,
  model_name TEXT NOT NULL,
  report JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

