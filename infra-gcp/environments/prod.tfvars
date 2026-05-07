project_id  = "dragonflyapp-prod"
region      = "us-central1"
environment = "prod"

api_image = "us-central1-docker.pkg.dev/dragonflyapp-prod/dragonfly/dragonfly-api:latest"

database_tier         = "db-custom-1-3840"
database_disk_size_gb = 20
min_instance_count    = 1
max_instance_count    = 20

cloud_run_invoker_members = ["allUsers"]
github_repository         = "bzinkan/Dragonfly"

