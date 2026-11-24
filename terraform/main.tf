terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "required_apis" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "logging.googleapis.com",
    "cloudtrace.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

resource "google_service_account" "concierge_sa" {
  account_id   = "exec-travel-concierge-sa"
  display_name = "Executive Travel Concierge ADK Runtime SA"
}

resource "google_secret_manager_secret" "travel_provider_api_key" {
  secret_id = "concierge-travel-provider-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required_apis]
}

resource "google_secret_manager_secret_iam_member" "concierge_secret_accessor" {
  secret_id = google_secret_manager_secret.travel_provider_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.concierge_sa.email}"
}

resource "google_project_iam_member" "concierge_least_privilege_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.concierge_sa.email}"
}

resource "google_cloud_run_v2_service" "concierge_runtime" {
  name     = "executive-travel-concierge"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = google_service_account.concierge_sa.email

    containers {
      image = var.container_image

      env {
        name  = "APP_ENV"
        value = "production"
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
        name  = "TRAVEL_PROVIDER_API_KEY_SECRET_ID"
        value = google_secret_manager_secret.travel_provider_api_key.secret_id
      }
      env {
        name  = "ENABLE_CLOUD_TRACE"
        value = "true"
      }
    }
  }

  depends_on = [google_project_service.required_apis]
}