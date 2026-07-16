# Release manifest

## Artifact identity

- Release date: 2026-07-16.
- Evaluation suite: `2026-07-16.1`, 120 cases × baseline and controlled runners.
- Runtime image: `us-central1-docker.pkg.dev/shawn-agent-control-260716/agent-control-plane/app@sha256:4ea79202b6c0d8b22f7453d7aa749af674a0e29416c4d917055bb4dc67b6c159`.
- Evaluation report: `evals/results/vertex-20260716.json`, SHA-256
  `b1074bfec68a996c63a886968407178cbcbf83611ac194eb96d9d14d22d2e2f2`.
- Walkthrough audio remaster: 2026-07-17, Volcengine `Jackson` narration with original procedural
  ambient music, 1920×1080 H.264/AAC, exactly five minutes, -16.1 LUFS integrated loudness,
  -1.4 dBFS true peak, SHA-256
  `dedb5904553dc770547ad929319653b6a62a90c741fe25b6ab0fd1f11b8c600c`.

## Acceptance evidence

- Vertex controlled result: 100% task success, 100% transient recovery, zero unsafe or
  unauthorized writes, zero tenant leakage, zero duplicate effects, 0% unsupported claims,
  100% retrieval recall@5, 12.17-second p95 excluding human wait, and $0.00043 average model
  cost per case. All ten encoded gates passed.
- Final Cloud Run v4 smoke: run `0ca92c7a-d4a0-425c-8ca8-baa95753abb9` completed after Identity
  Platform operator authentication, Vertex recommendation, LangGraph interrupt, separate approver
  authentication, IAM-authenticated MCP execution, and authoritative outcome verification.
- Negative cloud controls: anonymous API mutation returned 401; anonymous access to the MCP Cloud
  Run service returned 403. Temporary smoke-only TokenCreator access was removed after the run.
- Cloud evidence: the console screenshot SHA-256 is
  `6b095611c4bf12682844c1ad949e56f03a9a1891da6c2f0421abf1b34bdaa283`; the 17-span Cloud
  Trace screenshot SHA-256 is
  `886efaf4b544394523bdc99a869cff055284e84cca2c6b31b03eb74289c3c1d5`.

## Verification matrix

- Ruff: passed.
- Strict mypy: passed across 21 source files.
- Unit/API/MCP/auth/fault/evaluation tests: 19 passed; 2 integration-only tests deselected.
- PostgreSQL + MCP + persistent LangGraph checkpoint integration: 2 passed.
- React/TypeScript ESLint and production Vite build: passed.
- Terraform format and validation: passed.
- Architecture diagrams: independently reviewed against the diagram acceptance checklist; both
  passed after revision.

## Ephemeral cloud deployment

The release was captured in dedicated project `shawn-agent-control-260716` with control API
`https://agent-control-api-3nspora75a-uc.a.run.app` and private MCP service
`https://agent-control-tools-3nspora75a-uc.a.run.app`. The deployment used request-based
scale-to-zero, a $50 budget with $25/$40/$50 thresholds, and no static service-account key.

Terraform teardown completed on 2026-07-16 after public evidence capture and repository
publication. The first destroy attempt exposed a Cloud SQL ownership-order bug: the application
role owned 265 migrated objects. The Terraform dependency graph was corrected so destroy drops the
database before the user; the resumed destroy completed successfully. Final verification reported
Terraform state 0, Cloud Run 0, Cloud SQL 0, Artifact Registry 0, demo secrets 0, agent service
accounts 0, Workload Identity pools 0, API keys 0, and demo budgets 0. The dedicated empty project
and enabled service APIs remain; the two recorded service URLs are no longer live.

GitHub CI run [`29528495808`](https://github.com/qilinxiaoxiang/enterprise-agent-control-plane/actions/runs/29528495808)
passed lint, strict typing, unit/API/MCP/evaluation tests, real PostgreSQL+MCP integration, frontend
lint/build, Terraform validation, Docker build, deterministic evaluation, artifact upload, and
post-job cleanup.
