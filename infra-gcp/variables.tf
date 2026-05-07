variable "project_id" {
  description = "GCP project ID for this environment."
  type        = string
}

variable "region" {
  description = "Primary GCP region."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Dragonfly environment name."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging, or prod."
  }
}

variable "api_image" {
  description = "Container image deployed to Cloud Run."
  type        = string
}

variable "artifact_repository_id" {
  description = "Artifact Registry repository for backend containers."
  type        = string
  default     = "dragonfly"
}

variable "database_tier" {
  description = "Cloud SQL machine tier."
  type        = string
  default     = "db-g1-small"
}

variable "database_disk_size_gb" {
  description = "Cloud SQL disk size."
  type        = number
  default     = 10
}

variable "min_instance_count" {
  description = "Minimum Cloud Run instances."
  type        = number
  default     = 0
}

variable "max_instance_count" {
  description = "Maximum Cloud Run instances."
  type        = number
  default     = 5
}

variable "cloud_run_invoker_members" {
  description = "IAM members allowed to invoke the API service."
  type        = list(string)
  default     = ["allUsers"]
}

variable "github_repository" {
  description = "GitHub repository in owner/name form for Workload Identity Federation."
  type        = string
  default     = "bzinkan/Dragonfly"
}

variable "notification_channel_ids" {
  description = "Cloud Monitoring notification channel resource IDs."
  type        = list(string)
  default     = []
}

variable "billing_account_id" {
  description = "Billing account ID without the billingAccounts/ prefix. Empty disables budget creation."
  type        = string
  default     = ""
}

variable "project_number" {
  description = "Numeric GCP project number. Required only when billing_account_id is set."
  type        = string
  default     = ""
}

variable "monthly_budget_amount" {
  description = "Budget amount in USD for this environment."
  type        = number
  default     = 100
}
