variable "project_id" {
  description = "Target Google Cloud project ID for the Executive Travel Concierge."
  type        = string
}

variable "region" {
  description = "Primary Google Cloud region for Cloud Run and Vertex AI."
  type        = string
  default     = "us-central1"
}

variable "container_image" {
  description = "Artifact Registry container URI for the ADK concierge service."
  type        = string
  default     = "us-central1-docker.pkg.dev/example-project/agents/executive-travel-concierge:latest"
}
