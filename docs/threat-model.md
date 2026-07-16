# Threat model

## Protected assets

Tenant records, KYC/AML state, transfer state, policy integrity, approval identity, effect ledger,
checkpoint history, model prompts, credentials, and audit evidence.

## Trust boundaries and controls

| Threat | Boundary | Control | Verification |
|---|---|---|---|
| Prompt injection in case notes or retrieved text | Untrusted input → model | Notes are data-only; deterministic regex and policy stop; model has no direct effect authority | 20 security cases |
| Cross-tenant read/write | JWT/API → repositories and MCP | Required tenant claim; tenant match before collection; tenant on every record and effect | Auth tests and security cases |
| Stale, missing, or conflicting evidence | Source systems → decision | Authority/freshness metadata and deterministic conflict checks | 25 record-integrity cases |
| Forged or stale approval | Console → checkpoint resume | Approver claim plus exact proposed-action SHA-256 binding | Approval contract tests |
| Duplicate webhook or replay | External event → case/run | Signed body, event/idempotency equality, unique receipt | 20 idempotency/replay cases |
| Tool timeout after commit | MCP → effect system | Effect-status lookup; same idempotency key; no blind irreversible retry | Commit-after-timeout cases |
| Schema drift | MCP response → workflow | Pydantic contracts; safe stop; no effect inference | Fault-injection cases |
| Credential exfiltration | Runtime → public client | Secret Manager, IAM-only MCP invocation, no static service-account key | Terraform policy tests |
| Public mutation | Internet → control service | Anonymous access limited to precomputed synthetic runs; operator/approver claims for writes | API RBAC tests |

## Explicit non-goals

The project does not process real financial data, implement bank-grade identity proofing, or claim
regulatory certification. Synthetic policy illustrates control design, not legal advice.
