# Repository operating rules

- This repository contains only synthetic customer, policy, KYC/AML, and transfer data.
- Never copy OpenAssets source code, policies, customer data, names, or credentials here.
- A model recommendation never authorizes an effect. Deterministic policy and a human approval are separate gates.
- `release_transfer` is irreversible: on an ambiguous response, query the effect ledger before any retry.
- Resume claims may use only metrics produced by a committed evaluation report and a successful cloud smoke test.
- Run `make check` before publishing or deploying.

