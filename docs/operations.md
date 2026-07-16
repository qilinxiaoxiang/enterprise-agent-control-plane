# Operations and recovery

## Failure classes

- `429`, `5xx`, and pre-commit transport timeout: bounded retry with the same idempotency key.
- Irreversible-effect timeout or any post-commit disconnect: query `get_action_status` first.
- Schema drift, tenant mismatch, stale/conflicting evidence, unsupported model claim, approval-hash
  mismatch: stop and close as blocked; never infer success.
- Operator replay: create a child run linked by `replay_of`; preserve the original run and receipts.

## Telemetry

OpenTelemetry emits spans for HTTP, each LangGraph node, retrieval, model calls, MCP tools, retries,
checkpoints, approvals, and effects. Local OTLP goes to Jaeger. Cloud Run exports the same OTLP
spans and structured logs to Google Cloud Observability. Production uses synchronous span export
because request-based Cloud Run CPU can pause background batch workers after a response; the
release trace proves the approval → LangGraph → MCP → effect chain rather than relying on a
configuration-only claim.

## Evaluation recovery

The Vertex release runner treats HTTP 429/5xx and transport read/connect timeouts as transient.
Retries use the same case inputs with 30 → 60 → 120 second exponential backoff. Each completed
baseline/controlled pair is appended to an ignored partial checkpoint, so a throttled or interrupted
run resumes without discarding already measured cases. Final reports are written only after all 120
pairs aggregate and every hard gate is evaluated.

`VERTEX_THINKING_BUDGET=0` is deliberate for the bounded recommendation classifier. It avoids the
latency and cost of automatic reasoning after deterministic policy has already decided the
authorization boundary.

## Release gate

`make check`, Postgres integration, MCP contract, 120-case evaluation, Terraform validation, cloud
smoke, and evidence capture must all pass. A failed gate leaves the project labeled in progress and
prevents resume-metric promotion.
