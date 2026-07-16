# Five-minute walkthrough

## 0:00–0:35 — The problem

This is a control plane for a synthetic regulated-finance exception workflow, not a chatbot. The
agent can inspect KYC, AML, customer, and transfer records, but it cannot inherit write authority
from the model. The four effects are hold, remove hold, release transfer, and close case.

## 0:35–1:20 — Trust boundaries

Show the business-flow diagram. Point out the four independent decisions: authoritative evidence,
deterministic policy, model recommendation, and human approval. Explain source/freshness metadata,
tenant checks, cited policy, and the action hash that binds an approval to one exact effect.

## 1:20–2:20 — Normal run

Open the verified public run. Walk the ten-node timeline from intake through audit closure. Show the
retrieved policy IDs, pre-effect interrupt, approval identity, MCP receipt, and authoritative outcome
verification. Open Jaeger/Cloud Trace and follow the HTTP → LangGraph → retrieval → model → MCP span
chain.

## 2:20–3:20 — Failure recovery

Open the commit-timeout run. Explain why a transfer release is not “rolled back.” The network drops
after commit; the controller queries the effect ledger before considering a same-key retry. Restart
the control service while a second run is paused and resume it from the PostgreSQL LangGraph
checkpoint.

## 3:20–4:10 — Safe stops and security

Open the AML run and show that deterministic policy stops before approval or effect. Briefly show
the prompt-injection, cross-tenant, forged-approval, duplicate-webhook, and schema-drift tests. Note
that public viewers can inspect only precomputed synthetic runs; Firebase claims gate mutations.

## 4:10–5:00 — Measured result

Open the before/after report. Compare the same 120 cases, model, tools, and data under the direct
tool-calling baseline and the controlled graph: 39.2% versus 100% task success, 0% versus 100%
transient recovery, and +60.8 percentage points. Controlled unsafe/unauthorized writes, leakage,
and duplicate effects are all zero; p95 is 12.17 seconds and average model cost is $0.00043/case.
Close with the two-service Cloud Run deployment, IAM-only MCP invocation, Cloud SQL/pgvector, OIDC
deployment, and post-capture Terraform destroy policy.

The rendered narration is preserved in
[`evidence/walkthrough-narration.txt`](evidence/walkthrough-narration.txt).

## Audio production

- Narration uses the Volcengine `Jackson` English voice. No API credential is stored in the
  repository or media metadata.
- Background music is [“Upbeat Corporate Technology”](https://youtu.be/3XjvDdpANJM) by Grand
  Project / Roman Dudchyk Music, used under the creator's free non-monetized video-use terms
  described on the source page. The source track is kept outside the repository and passed to
  `scripts/render_walkthrough_media.py` with `--bgm`.
- The music retains its original tempo. Two passes beginning 24 seconds into the source are joined
  with a five-second crossfade, then mixed as a constant low-volume bed. There is no speech-driven
  ducking; one fixed final gain preserves the same narration-to-music ratio for all five sections.
