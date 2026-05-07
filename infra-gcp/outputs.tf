output "api_service_name" {
  value = google_cloud_run_v2_service.api.name
}

output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "artifact_repository" {
  value = google_artifact_registry_repository.backend.name
}

output "cloudbuild_source_bucket" {
  value = google_storage_bucket.cloudbuild_source.name
}

output "cloud_sql_connection_name" {
  value = google_sql_database_instance.main.connection_name
}

output "photos_bucket" {
  value = google_storage_bucket.photos.name
}

output "github_workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "github_deploy_service_account" {
  value = google_service_account.github_deploy.email
}

