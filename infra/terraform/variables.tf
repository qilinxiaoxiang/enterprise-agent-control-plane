variable "project_id" {
  description = "Dedicated GCP project for the public demo."
  type        = string
}

variable "billing_account_id" {
  description = "Billing account used by budget alerts."
  type        = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "repository_owner" {
  type    = string
  default = "qilinxiaoxiang"
}

variable "repository_name" {
  type    = string
  default = "enterprise-agent-control-plane"
}

variable "control_image" {
  description = "Immutable control API image URL including digest or tag."
  type        = string
}

variable "tool_image" {
  description = "Immutable MCP tool-server image URL including digest or tag."
  type        = string
}

variable "google_oauth_client_id" {
  description = "Google OAuth web client ID for Firebase/Identity Platform."
  type        = string
  default     = ""
}

variable "google_oauth_client_secret" {
  description = "Google OAuth web client secret."
  type        = string
  sensitive   = true
  default     = ""
}

