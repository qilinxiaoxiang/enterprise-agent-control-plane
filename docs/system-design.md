# System design

## Invariants

1. Authoritative records and model context are different stores. Model context never becomes a
   source of truth merely because it is recent or confidently phrased.
2. Every evidence record carries source, tenant, observation time, expiry, and authority metadata.
3. The policy engine authorizes; the model recommends; a named approver decides; the tool server
   executes. No layer can silently inherit the authority of another.
4. Every consequential tool call is bound to an exact action hash and idempotency key.
5. `release_transfer` is irreversible. A timeout causes an effect-ledger lookup before a same-key
   retry can be considered.
6. A case closes only after an authoritative outcome check.

## Workflow

The compiled LangGraph follows one fixed path:

`intake validation → authoritative record collection → policy retrieval → conflict/freshness
checks → deterministic policy decision → LLM recommendation → human interrupt → idempotent
execution → outcome verification → audit closure`.

Deterministic stops route to a blocked audit closure. LangGraph checkpoints preserve the exact
pre-approval state; resume carries a signed decision bound to the proposed-action hash. A replay
creates a traceable child run and never mutates the original audit record.

## Data boundaries

- `cases`, `source_records`, `policy_chunks`, `tool_invocations`, `approvals`, `effect_ledger`, and
  `eval_runs` are first-class application entities.
- LangGraph checkpoint tables live in a separate `langgraph_checkpoints` schema.
- Policy embeddings are 768-dimensional `gemini-embedding-001` vectors indexed by pgvector HNSW.
- All query and effect paths are tenant-scoped before content reaches the model.

See `docs/diagrams/` for the reviewed business-flow and GCP runtime views.

