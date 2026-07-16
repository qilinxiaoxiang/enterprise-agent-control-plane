# Release evaluation: direct agent vs controlled graph

Release suite `2026-07-16.1` ran 120 versioned synthetic cases against the same Vertex AI
`gemini-2.5-flash` recommendation provider, policy corpus, tool semantics, and source records.
The model used temperature `0` and thinking budget `0`. The only experimental variable was the
reliability layer: the baseline connected recommendation directly to tools, while the controlled
runner added deterministic authorization, LangGraph checkpoints, human approval, tenant controls,
idempotency, and outcome verification.

![Release evaluation summary](evidence/evaluation-summary.png)

| Metric | Direct tool-calling baseline | Controlled LangGraph | Release gate |
| --- | ---: | ---: | ---: |
| Task success | 39.2% | **100.0%** | ≥90% |
| Transient-failure recovery | 0.0% | **100.0%** | ≥95% |
| Unsafe writes | 45 | **0** | 0 |
| Unauthorized writes | 12 | **0** | 0 |
| Cross-tenant leakages | 13 | **0** | 0 |
| Duplicate side effects | 8 | **0** | 0 |
| Unsupported-claim rate | 0.0% | **0.0%** | ≤2% |
| Retrieval recall@5 | 100.0% | **100.0%** | ≥90% |
| p95 latency, excluding human wait | 12.61 s | **12.17 s** | ≤15 s |
| Average model cost per case | $0.000379 | **$0.000429** | ≤$0.03 |

The controlled system improved task success by **60.8 percentage points** and reduced measured
failures by **100%**. All ten encoded hard gates passed. The controlled path's small cost increase
($0.000050/case) buys the model call on safely stopped cases while remaining less than 1.5% of the
$0.03 budget.

## Case composition

- 30 normal business paths.
- 25 missing, stale, or conflicting record paths.
- 25 timeout, 429, 5xx, schema-drift, and post-commit disconnect paths.
- 20 tenant-isolation, authorization, and prompt-injection attacks.
- 20 duplicate-webhook, approval, compensation, and replay paths.

## Interpretation

The comparison is a controlled engineering benchmark, not a claim about all agents or production
financial outcomes. It demonstrates that the specified controls prevent the injected failure modes
under a fixed synthetic workload. Latency includes model and control-plane execution but excludes
time spent waiting for a human approval. Cost is calculated from measured input/output tokens using
the release-date Gemini 2.5 Flash standard Vertex pricing.

For reproducible paired fault injection, the 120-case harness uses in-process deterministic policy
retrieval and a synthetic gateway with the same domain contracts and effect semantics. It does not
claim to benchmark networked pgvector or MCP latency. Those production adapters are validated by
the PostgreSQL+pgvector+MCP integration tests and the authenticated two-service Cloud Run smoke.

The machine-readable source of record is
[`evals/results/vertex-20260716.json`](../evals/results/vertex-20260716.json), SHA-256
`b1074bfec68a996c63a886968407178cbcbf83611ac194eb96d9d14d22d2e2f2`.
