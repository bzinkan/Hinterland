data "google_project" "current" {
  project_id = var.project_id
}

locals {
  service_name               = "dragonfly-api"
  database_instance          = "dragonfly-postgres-${var.environment}"
  database_name              = "dragonfly"
  database_user              = "dragonfly"
  photos_bucket              = "dragonfly-photos-${var.environment}-${var.project_id}"
  cloudbuild_source_bucket   = "dragonfly-build-source-${var.environment}-${var.project_id}"
  cloudbuild_service_account = "${data.google_project.current.number}-compute@developer.gserviceaccount.com"
  github_pool_id             = "github-${var.environment}"
  github_provider_id         = "github-provider"
  cleanup_smoke_job_name     = "dragonfly-cleanup-smoke"
  enabled_service_list = toset([
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    "compute.googleapis.com",
    "firebase.googleapis.com",
    "identitytoolkit.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "orgpolicy.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
  ])
}

resource "google_project_service" "enabled" {
  for_each = local.enabled_service_list

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "backend" {
  location      = var.region
  repository_id = var.artifact_repository_id
  description   = "Dragonfly backend container images"
  format        = "DOCKER"

  depends_on = [google_project_service.enabled]
}

resource "google_service_account" "api" {
  account_id   = "dragonfly-api-${var.environment}"
  display_name = "Dragonfly API ${var.environment}"

  depends_on = [google_project_service.enabled]
}

resource "random_password" "database" {
  length  = 32
  special = true
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "dragonfly-${var.environment}-database-password"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.database.result
}

resource "google_sql_database_instance" "main" {
  name             = local.database_instance
  database_version = "POSTGRES_16"
  region           = var.region

  deletion_protection = var.environment == "prod"

  settings {
    # ENTERPRISE keeps the legacy shared-core tiers (db-g1-small, etc.) which
    # ENTERPRISE_PLUS rejects. Cheap dev posture; revisit for prod when we
    # care about the perf/availability features ENTERPRISE_PLUS adds.
    edition           = "ENTERPRISE"
    tier              = var.database_tier
    availability_type = var.environment == "prod" ? "REGIONAL" : "ZONAL"
    disk_size         = var.database_disk_size_gb
    disk_type         = "PD_SSD"

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "07:00"
    }

    maintenance_window {
      day          = 7
      hour         = 8
      update_track = "stable"
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_sql_database" "app" {
  name     = local.database_name
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "app" {
  name     = local.database_user
  instance = google_sql_database_instance.main.name
  password = random_password.database.result
}

resource "google_storage_bucket" "photos" {
  name                        = local.photos_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = var.environment != "prod"

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = 1
      matches_prefix = ["pending/"]
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = 90
      matches_prefix = ["quarantine/"]
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket" "cloudbuild_source" {
  name                        = local.cloudbuild_source_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = var.environment != "prod"

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age            = 7
      matches_prefix = ["source/"]
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "api_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "api_db_password" {
  secret_id = google_secret_manager_secret.db_password.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_storage_bucket_iam_member" "api_photos_object_admin" {
  bucket = google_storage_bucket.photos.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.api.email}"
}

# Firebase Admin SDK on the runtime SA. Needed for set_custom_user_claims
# (parent-signup, kid create) and create_user (kid create). Includes
# firebaseauth.users.get which the SDK also uses when check_revoked=True.
resource "google_project_iam_member" "api_firebaseauth_admin" {
  project = var.project_id
  role    = "roles/firebaseauth.admin"
  member  = "serviceAccount:${google_service_account.api.email}"
}

# Lets the runtime SA sign blobs against itself, which is what
# firebase_admin.auth.create_custom_token does internally (via
# iam.signBlob) when no explicit service-account JSON is provided. This
# is the standard pattern for ADC-based custom token minting.
resource "google_service_account_iam_member" "api_token_creator_self" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.api.email}"
}

resource "google_cloud_run_v2_service" "api" {
  name                = local.service_name
  location            = var.region
  deletion_protection = var.environment == "prod"
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.api.email

    scaling {
      min_instance_count = var.min_instance_count
      max_instance_count = var.max_instance_count
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.main.connection_name]
      }
    }

    containers {
      image = var.api_image

      ports {
        container_port = 8080
      }

      env {
        name  = "DRAGONFLY_ENV"
        value = var.environment
      }

      env {
        name  = "DRAGONFLY_GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "DRAGONFLY_PHOTOS_BUCKET"
        value = google_storage_bucket.photos.name
      }

      env {
        name  = "DRAGONFLY_CLOUD_SQL_INSTANCE"
        value = google_sql_database_instance.main.connection_name
      }

      env {
        name  = "DRAGONFLY_DATABASE_HOST"
        value = "/cloudsql/${google_sql_database_instance.main.connection_name}"
      }

      env {
        name  = "DRAGONFLY_DATABASE_NAME"
        value = google_sql_database.app.name
      }

      env {
        name  = "DRAGONFLY_DATABASE_USER"
        value = google_sql_user.app.name
      }

      env {
        name = "DRAGONFLY_DATABASE_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "DRAGONFLY_READINESS_DATABASE_REQUIRED"
        value = "true"
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }
  }

  depends_on = [
    google_project_iam_member.api_cloudsql_client,
    google_secret_manager_secret_iam_member.api_db_password,
    google_sql_database.app,
    google_sql_user.app,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "api_invokers" {
  for_each = toset(var.cloud_run_invoker_members)

  project  = var.project_id
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = each.key

  # If the member set includes allUsers / allAuthenticatedUsers, the org
  # policy override below must apply first or the binding will be silently
  # dropped by iam.allowedPolicyMemberDomains. depends_on is a no-op when
  # cloud_run_public is false (count = 0).
  depends_on = [google_org_policy_policy.domain_restricted_sharing]
}

# Optional project-scope override of iam.allowedPolicyMemberDomains so
# allUsers and allAuthenticatedUsers can be granted Cloud Run invoker.
# Required for public mobile API access; gated by var.cloud_run_public so
# staging/prod can opt in independently. See ADR 0008.
resource "google_org_policy_policy" "domain_restricted_sharing" {
  count = var.cloud_run_public ? 1 : 0

  name   = "projects/${data.google_project.current.project_id}/policies/iam.allowedPolicyMemberDomains"
  parent = "projects/${data.google_project.current.project_id}"

  spec {
    inherit_from_parent = false
    reset               = false
    rules {
      allow_all = "TRUE"
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_service_account" "github_deploy" {
  account_id   = "github-deploy-${var.environment}"
  display_name = "GitHub deploy ${var.environment}"

  depends_on = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = local.github_pool_id
  display_name              = "GitHub ${var.environment}"
  description               = "GitHub Actions federation for Dragonfly ${var.environment}"

  depends_on = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = local.github_provider_id
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.actor"      = "assertion.actor"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  attribute_condition = "attribute.repository == '${var.github_repository}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_deploy_wif" {
  service_account_id = google_service_account.github_deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_service_account_iam_member" "github_deploy_token_creator" {
  service_account_id = google_service_account.github_deploy.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.github_deploy.email}"
}

resource "google_service_account_iam_member" "github_deploy_wif_token_creator" {
  service_account_id = google_service_account.github_deploy.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_project_iam_member" "github_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.github_deploy.email}"
}

resource "google_project_iam_member" "github_cloudbuild" {
  project = var.project_id
  role    = "roles/cloudbuild.builds.editor"
  member  = "serviceAccount:${google_service_account.github_deploy.email}"
}

resource "google_project_iam_member" "github_service_usage" {
  project = var.project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = "serviceAccount:${google_service_account.github_deploy.email}"
}

# `terraform plan` in CI needs IAM-policy read access on the resources
# already in state. roles/iam.securityReviewer covers the SA IAM reads
# (iam.serviceAccounts.getIamPolicy) that the google provider performs
# on every refresh.
resource "google_project_iam_member" "github_deploy_security_reviewer" {
  project = var.project_id
  role    = "roles/iam.securityReviewer"
  member  = "serviceAccount:${google_service_account.github_deploy.email}"
}

# Broader resource-read role for `terraform plan` to refresh the rest of
# the state (workloadIdentityPools.get, etc.). Coarser than ideal; the
# alternative is granting six narrower viewer roles. Revisit if a
# narrower bundle becomes worth the maintenance.
resource "google_project_iam_member" "github_deploy_viewer" {
  project = var.project_id
  role    = "roles/viewer"
  member  = "serviceAccount:${google_service_account.github_deploy.email}"
}

# Lets gcloud's source-deploy flow read the Cloud Build staging bucket
# and push artifacts. Cloud Build switched from its own SA to the
# Compute Engine default SA in 2024, and that SA needs the builds.builder
# role explicitly to operate. Required by the workflow's Build image
# step using `gcloud builds submit --tag`.
resource "google_project_iam_member" "cloudbuild_compute_builds_builder" {
  project = var.project_id
  role    = "roles/cloudbuild.builds.builder"
  member  = "serviceAccount:${local.cloudbuild_service_account}"
}

resource "google_artifact_registry_repository_iam_member" "github_artifact_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.backend.location
  repository = google_artifact_registry_repository.backend.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.github_deploy.email}"
}

# repoAdmin (specifically tags.delete + tags.create) is needed for the
# deploy workflow's "Tag image as :latest" step, which moves the
# floating tag to the freshly-built SHA. roles/artifactregistry.writer
# does NOT include tags.delete, so the move was failing before this.
resource "google_artifact_registry_repository_iam_member" "github_deploy_repo_admin" {
  project    = var.project_id
  location   = google_artifact_registry_repository.backend.location
  repository = google_artifact_registry_repository.backend.name
  role       = "roles/artifactregistry.repoAdmin"
  member     = "serviceAccount:${google_service_account.github_deploy.email}"
}

resource "google_storage_bucket_iam_member" "github_cloudbuild_source_object_admin" {
  bucket = google_storage_bucket.cloudbuild_source.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.github_deploy.email}"
}

resource "google_storage_bucket_iam_member" "github_cloudbuild_source_bucket_reader" {
  bucket = google_storage_bucket.cloudbuild_source.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_service_account.github_deploy.email}"
}

resource "google_storage_bucket_iam_member" "cloudbuild_source_object_viewer" {
  bucket = google_storage_bucket.cloudbuild_source.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${local.cloudbuild_service_account}"
}

resource "google_storage_bucket_iam_member" "cloudbuild_source_bucket_reader" {
  bucket = google_storage_bucket.cloudbuild_source.name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${local.cloudbuild_service_account}"
}

resource "google_artifact_registry_repository_iam_member" "cloudbuild_artifact_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.backend.location
  repository = google_artifact_registry_repository.backend.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${local.cloudbuild_service_account}"
}

resource "google_service_account_iam_member" "github_cloudbuild_service_account_user" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${local.cloudbuild_service_account}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.github_deploy.email}"
}

resource "google_service_account_iam_member" "github_service_account_user" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.github_deploy.email}"
}

resource "google_monitoring_alert_policy" "api_5xx" {
  count = length(var.notification_channel_ids) > 0 ? 1 : 0

  display_name = "Dragonfly ${var.environment} API 5xx"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run 5xx responses"

    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.label.response_code_class=\"5xx\" AND resource.label.service_name=\"${local.service_name}\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = var.notification_channel_ids
}

# Phase 11 alarms. Same notification-channels gate as api_5xx so the
# resources only materialize once a channel is wired up.

# p95 request latency above 1500ms for 10 minutes. Dispatcher budget is
# 300ms (per docs/dispatcher.md), with the rest spent on the SQL writes
# + signed-URL gen. 1500ms is the "something's wrong, not just iNat
# being slow" threshold.
resource "google_monitoring_alert_policy" "api_latency_p95" {
  count = length(var.notification_channel_ids) > 0 ? 1 : 0

  display_name = "Dragonfly ${var.environment} API p95 latency"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run p95 request latency"

    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_latencies\" AND resource.label.service_name=\"${local.service_name}\""
      duration        = "600s"
      comparison      = "COMPARISON_GT"
      threshold_value = 1500

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_PERCENTILE_95"
      }
    }
  }

  notification_channels = var.notification_channel_ids
}

# Cloud SQL CPU above 80% for 5 minutes. Cloud SQL g1-small has 1 shared
# vCPU; sustained 80%+ means the kid request path is queueing on it and
# we need to either tune queries or move to a perf-optimized tier.
resource "google_monitoring_alert_policy" "cloud_sql_cpu" {
  count = length(var.notification_channel_ids) > 0 ? 1 : 0

  display_name = "Dragonfly ${var.environment} Cloud SQL CPU"
  combiner     = "OR"

  conditions {
    display_name = "Cloud SQL CPU utilization"

    condition_threshold {
      filter          = "resource.type=\"cloudsql_database\" AND resource.label.database_id=\"${var.project_id}:${local.database_instance}\" AND metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0.80

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  notification_channels = var.notification_channel_ids
}

# Cloud Run instance count above 8 for 10 minutes. Service is configured
# with max 5 in dev (per dev.tfvars) so >8 should be impossible -- this
# alerts if someone tweaks max_instance_count without thinking through
# the database connection pool implications.
resource "google_monitoring_alert_policy" "cloud_run_instances" {
  count = length(var.notification_channel_ids) > 0 ? 1 : 0

  display_name = "Dragonfly ${var.environment} Cloud Run instance count"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run instance count"

    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/container/instance_count\" AND resource.label.service_name=\"${local.service_name}\""
      duration        = "600s"
      comparison      = "COMPARISON_GT"
      threshold_value = 8

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  notification_channels = var.notification_channel_ids
}

# Dogfood dashboard. One-pane view for the closed-beta period: request
# rate, latency p50/p95, instance count, Cloud SQL CPU. Not gated on
# notification channels since dashboards have no per-channel cost.
resource "google_monitoring_dashboard" "dogfood" {
  dashboard_json = jsonencode({
    displayName = "Dragonfly ${var.environment} dogfood"
    gridLayout = {
      columns = 2
      widgets = [
        {
          title = "Request rate (req/s)"
          xyChart = {
            dataSets = [{
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_count\" AND resource.label.service_name=\"${local.service_name}\""
                  aggregation = {
                    alignmentPeriod    = "60s"
                    perSeriesAligner   = "ALIGN_RATE"
                    crossSeriesReducer = "REDUCE_SUM"
                  }
                }
              }
            }]
          }
        },
        {
          title = "Request latency (p50, p95, p99 ms)"
          xyChart = {
            dataSets = [
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_latencies\" AND resource.label.service_name=\"${local.service_name}\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_DELTA"
                      crossSeriesReducer = "REDUCE_PERCENTILE_50"
                    }
                  }
                }
              },
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_latencies\" AND resource.label.service_name=\"${local.service_name}\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_DELTA"
                      crossSeriesReducer = "REDUCE_PERCENTILE_95"
                    }
                  }
                }
              },
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_latencies\" AND resource.label.service_name=\"${local.service_name}\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_DELTA"
                      crossSeriesReducer = "REDUCE_PERCENTILE_99"
                    }
                  }
                }
              }
            ]
          }
        },
        {
          title = "Cloud Run instance count"
          xyChart = {
            dataSets = [{
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/container/instance_count\" AND resource.label.service_name=\"${local.service_name}\""
                  aggregation = {
                    alignmentPeriod    = "60s"
                    perSeriesAligner   = "ALIGN_MAX"
                    crossSeriesReducer = "REDUCE_SUM"
                  }
                }
              }
            }]
          }
        },
        {
          title = "Cloud SQL CPU"
          xyChart = {
            dataSets = [{
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "resource.type=\"cloudsql_database\" AND resource.label.database_id=\"${var.project_id}:${local.database_instance}\" AND metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\""
                  aggregation = {
                    alignmentPeriod  = "60s"
                    perSeriesAligner = "ALIGN_MEAN"
                  }
                }
              }
            }]
          }
        }
      ]
    }
  })
}

# Nightly drain of the smoke+*@dragonfly-test.invalid users that
# scripts/smoke_phase4.py creates after every deploy. The Cloud Run Job
# itself (dragonfly-cleanup-smoke) is currently created out-of-band via
# REST -- see docs/runbook.md "Cleanup Smoke Test Users". Importing the
# job into Terraform is a follow-up; the schedule is the urgent piece
# since accumulation is otherwise manual to drain.
#
# Dev only: smoke runs only after dev deploys, so staging/prod don't
# accumulate. Gated on environment so a future opt-in is one tfvars line.
resource "google_service_account" "scheduler" {
  count = var.environment == "dev" ? 1 : 0

  account_id   = "dragonfly-scheduler-${var.environment}"
  display_name = "Dragonfly Cloud Scheduler ${var.environment}"

  depends_on = [google_project_service.enabled]
}

# Scheduler SA needs run.invoker on the cleanup job to execute it.
# References the job by name string (not resource attribute) since the
# job lives outside Terraform today.
resource "google_cloud_run_v2_job_iam_member" "scheduler_cleanup_invoker" {
  count = var.environment == "dev" ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = local.cleanup_smoke_job_name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler[0].email}"
}

# 09:00 UTC = 04:00 America/Chicago (CDT) / 03:00 CST. Well clear of the
# deploy window and the post-deploy smoke step, so cleanup never races a
# fresh smoke user it just created.
resource "google_cloud_scheduler_job" "cleanup_smoke" {
  count = var.environment == "dev" ? 1 : 0

  name        = "dragonfly-cleanup-smoke-nightly"
  description = "Nightly drain of smoke+*@dragonfly-test.invalid users"
  schedule    = "0 9 * * *"
  time_zone   = "Etc/UTC"
  region      = var.region

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${local.cleanup_smoke_job_name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler[0].email
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_cloud_run_v2_job_iam_member.scheduler_cleanup_invoker,
  ]
}

resource "google_billing_budget" "environment" {
  count = var.billing_account_id == "" ? 0 : 1

  billing_account = "billingAccounts/${var.billing_account_id}"
  display_name    = "Dragonfly ${var.environment}"

  budget_filter {
    projects = var.project_number == "" ? [] : ["projects/${var.project_number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = var.monthly_budget_amount
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }

  threshold_rules {
    threshold_percent = 0.9
  }

  threshold_rules {
    threshold_percent = 1.0
  }
}
