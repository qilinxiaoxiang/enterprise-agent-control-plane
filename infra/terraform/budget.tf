resource "google_billing_budget" "demo" {
  billing_account = var.billing_account_id
  display_name    = "Enterprise agent demo $50 cap"

  budget_filter {
    projects = ["projects/${data.google_project.current.number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = "50"
    }
  }

  threshold_rules { threshold_percent = 0.5 }
  threshold_rules { threshold_percent = 0.8 }
  threshold_rules { threshold_percent = 1.0 }
}

data "google_project" "current" {
  project_id = var.project_id
}

