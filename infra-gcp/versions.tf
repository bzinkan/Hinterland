terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state on GCS in dragonflyapp-495423.
  # Bucket has versioning + uniform bucket-level access + public access
  # prevention. State locking is native to the gcs backend.
  backend "gcs" {
    bucket = "dragonflyapp-tfstate"
    prefix = "infra-gcp"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

