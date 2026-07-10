"""Server-side admin tasks designed to run as one-off Container Apps Jobs.

Each module is invoked via `python -m admin.<task>` from /app inside the
deployed image (which is the workdir set by backend/Dockerfile).
"""
