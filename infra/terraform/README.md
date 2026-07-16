# GCP deployment

The stack creates two Cloud Run services, Cloud SQL PostgreSQL 16 with pgvector, Artifact Registry,
Secret Manager, Vertex/Trace APIs, Google Identity Platform JWTs, GitHub OIDC Workload Identity, and a
$50 budget with 50% ($25), 80% ($40), and 100% alerts. Neither runtime nor CI uses a static service-
account key.

The control service is public only at the HTTP layer; anonymous callers can read precomputed
synthetic runs. Mutations require Firebase tokens with tenant plus `operator` or `approver` custom
claims. The MCP service is network-addressable but grants `roles/run.invoker` only to the control
runtime service account, so every call still requires a Google-signed service identity token.

```bash
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform validate
terraform plan -out=release.tfplan
terraform apply release.tfplan
```

Before apply, push immutable `control` and `tools` images and set their exact URLs. Initialize the
application schema with `infra/sql/001_init.sql`, then run the deployment smoke in
`scripts/cloud-smoke.sh`.

Terraform is the explicit environment bootstrap and teardown owner. After bootstrap, the manual
`deploy-gcp` GitHub Actions workflow uses short-lived OIDC credentials to build an image, resolve
its immutable digest, roll both existing Cloud Run services, and smoke `/v1/health`. It deliberately
does not run Terraform without durable state.

After evaluation and recording are complete, preserve screenshots/reports in the release and run:

```bash
terraform destroy
```

The release checklist requires destroy within 72 hours. Cloud SQL has deletion protection disabled
specifically for this short-lived synthetic portfolio environment.

The application role owns migrated database objects. Terraform intentionally creates the SQL user
before the database, which reverses the teardown order: destroy drops the database and owned
objects before deleting the user. This ordering was exercised by the accepted release teardown.
