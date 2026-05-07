project_id     = "dragonflyapp-prod"
project_number = "18296780658"
region         = "us-central1"
environment    = "prod"

api_image = "us-central1-docker.pkg.dev/dragonflyapp-prod/dragonfly/dragonfly-api:latest"

database_tier         = "db-custom-1-3840"
database_disk_size_gb = 20
min_instance_count    = 1
max_instance_count    = 20

cloud_run_invoker_members = [
  "domain:dragonfly-app.net",
  "serviceAccount:github-deploy-prod@dragonflyapp-prod.iam.gserviceaccount.com",
]
github_repository = "bzinkan/Dragonfly"

billing_account_id    = "011393-C6CD59-B4C81C"
monthly_budget_amount = 250

