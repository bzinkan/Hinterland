project_id     = "dragonflyapp-staging"
project_number = "224397195622"
region         = "us-central1"
environment    = "staging"

api_image = "us-central1-docker.pkg.dev/dragonflyapp-staging/dragonfly/dragonfly-api:latest"

database_tier         = "db-g1-small"
database_disk_size_gb = 10
min_instance_count    = 0
max_instance_count    = 5

cloud_run_invoker_members = [
  "domain:dragonfly-app.net",
  "serviceAccount:github-deploy-staging@dragonflyapp-staging.iam.gserviceaccount.com",
]
github_repository = "bzinkan/Dragonfly"

billing_account_id    = "011393-C6CD59-B4C81C"
monthly_budget_amount = 100

