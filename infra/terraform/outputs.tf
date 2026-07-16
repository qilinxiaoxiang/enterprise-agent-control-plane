output "control_url" {
  value = google_cloud_run_v2_service.control.uri
}

output "private_tool_url" {
  value = google_cloud_run_v2_service.tools.uri
}

output "artifact_repository" {
  value = google_artifact_registry_repository.images.name
}

output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account" {
  value = google_service_account.deployer.email
}

output "firebase_api_key" {
  value     = google_apikeys_key.firebase_client.key_string
  sensitive = true
}
