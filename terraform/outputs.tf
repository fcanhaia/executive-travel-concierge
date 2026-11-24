output "service_url" {
  description = "Deployed Cloud Run endpoint for the Executive Travel Concierge."
  value       = google_cloud_run_v2_service.concierge_runtime.uri
}

output "agent_service_account" {
  description = "Dedicated least-privilege IAM service account email."
  value       = google_service_account.concierge_sa.email
}
