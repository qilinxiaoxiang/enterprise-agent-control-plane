locals {
  services = toset([
    "apikeys.googleapis.com",
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "firebase.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "identitytoolkit.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
    "sts.googleapis.com",
    "cloudtrace.googleapis.com",
  ])
  database_url = format(
    "postgresql://control:%s@/control?host=/cloudsql/%s",
    random_password.database.result,
    google_sql_database_instance.main.connection_name,
  )
}

resource "google_project_service" "required" {
  for_each           = local.services
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "agent-control-plane"
  description   = "Container images for the synthetic reliability demo"
  format        = "DOCKER"
  depends_on    = [google_project_service.required]
}

resource "random_password" "database" {
  length  = 28
  special = false
}

resource "random_password" "webhook" {
  length  = 40
  special = false
}

resource "google_sql_database_instance" "main" {
  name                = "agent-control-pg16"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = false

  settings {
    edition           = "ENTERPRISE"
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = 10
    disk_autoresize   = true

    backup_configuration {
      enabled = false
    }

    ip_configuration {
      ipv4_enabled = true
      ssl_mode     = "ENCRYPTED_ONLY"
    }

  }

  depends_on = [google_project_service.required]
}

resource "google_sql_database" "control" {
  name     = "control"
  instance = google_sql_database_instance.main.name
  # The application user owns migrated objects. Create the user first so the
  # reverse destroy order drops the database (and its objects) before the user.
  depends_on = [google_sql_user.control]
}

resource "google_sql_user" "control" {
  name     = "control"
  instance = google_sql_database_instance.main.name
  password = random_password.database.result
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "agent-control-database-url"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = local.database_url
}

resource "google_secret_manager_secret" "webhook" {
  secret_id = "agent-control-webhook-secret"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "webhook" {
  secret      = google_secret_manager_secret.webhook.id
  secret_data = random_password.webhook.result
}

resource "google_service_account" "control" {
  account_id   = "agent-control-api"
  display_name = "Agent control API runtime"
}

resource "google_service_account" "tools" {
  account_id   = "agent-control-tools"
  display_name = "Private synthetic MCP runtime"
}

resource "google_service_account" "deployer" {
  account_id   = "agent-control-deployer"
  display_name = "GitHub Actions workload-identity deployer"
}

resource "google_project_iam_member" "control_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/cloudsql.client",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/secretmanager.secretAccessor",
    "roles/cloudtrace.agent",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.control.email}"
}

resource "google_project_iam_member" "tool_roles" {
  for_each = toset([
    "roles/cloudsql.client",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/secretmanager.secretAccessor",
    "roles/cloudtrace.agent",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.tools.email}"
}

resource "google_cloud_run_v2_service" "tools" {
  name     = "agent-control-tools"
  location = var.region
  # Network-reachable for service-to-service identity tokens; IAM grants invoker
  # only to the control runtime service account.
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.tools.email
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.main.connection_name]
      }
    }
    containers {
      image   = var.tool_image
      command = ["synthetic-tool-server"]
      ports {
        container_port = 8080
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      env {
        name  = "APP_ENV"
        value = "production"
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }
      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_v2_service" "control" {
  name                = "agent-control-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.control.email
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.main.connection_name]
      }
    }
    containers {
      image   = var.control_image
      command = ["control-api"]
      ports {
        container_port = 8080
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      env {
        name  = "APP_ENV"
        value = "production"
      }
      env {
        name  = "AUTH_MODE"
        value = "firebase"
      }
      env {
        name  = "MODEL_PROVIDER"
        value = "vertex"
      }
      env {
        name  = "REPOSITORY_BACKEND"
        value = "postgres"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "true"
      }
      env {
        name  = "VERTEX_THINKING_BUDGET"
        value = "0"
      }
      env {
        name  = "MCP_SERVER_URL"
        value = "${google_cloud_run_v2_service.tools.uri}/mcp"
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "WEBHOOK_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.webhook.secret_id
            version = "latest"
          }
        }
      }
      resources {
        limits = { cpu = "1", memory = "1Gi" }
      }
    }
  }

  depends_on = [
    google_project_iam_member.control_roles,
    google_secret_manager_secret_version.database_url,
    google_secret_manager_secret_version.webhook,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public_viewer" {
  project  = var.project_id
  location = google_cloud_run_v2_service.control.location
  name     = google_cloud_run_v2_service.control.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "control_calls_tools" {
  project  = var.project_id
  location = google_cloud_run_v2_service.tools.location
  name     = google_cloud_run_v2_service.tools.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.control.email}"
}

resource "google_identity_platform_config" "default" {
  provider = google-beta
  project  = var.project_id
  sign_in {
    anonymous {
      enabled = false
    }
    email {
      enabled           = false
      password_required = true
    }
  }
  depends_on = [google_project_service.required]
}

resource "google_identity_platform_default_supported_idp_config" "google" {
  count         = var.google_oauth_client_id == "" ? 0 : 1
  provider      = google-beta
  project       = var.project_id
  idp_id        = "google.com"
  enabled       = true
  client_id     = var.google_oauth_client_id
  client_secret = var.google_oauth_client_secret
  depends_on    = [google_identity_platform_config.default]
}
