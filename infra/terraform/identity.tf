resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "agent-control-github"
  display_name              = "Agent Control GitHub Actions"
  description               = "Short-lived GitHub OIDC credentials; no service-account key"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub repository provider"
  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.actor"            = "assertion.actor"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
  }
  attribute_condition = "assertion.repository == '${var.repository_owner}/${var.repository_name}'"
  oidc { issuer_uri = "https://token.actions.githubusercontent.com" }
}

resource "google_service_account_iam_member" "github_impersonates_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member = format(
    "principalSet://iam.googleapis.com/%s/attribute.repository/%s/%s",
    google_iam_workload_identity_pool.github.name,
    var.repository_owner,
    var.repository_name,
  )
}

resource "google_project_iam_member" "deployer_roles" {
  for_each = toset([
    "roles/artifactregistry.writer",
    "roles/cloudsql.admin",
    "roles/iam.serviceAccountUser",
    "roles/run.admin",
    "roles/secretmanager.admin",
    "roles/serviceusage.serviceUsageAdmin",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_apikeys_key" "firebase_client" {
  name         = "agent-control-firebase-client"
  display_name = "Agent Control Firebase client"

  restrictions {
    api_targets {
      service = "identitytoolkit.googleapis.com"
    }
  }

  depends_on = [google_project_service.required]
}
