# Production deployment

`docker-compose.prod.yml` is a standalone production-oriented stack for FastAPI,
PostgreSQL, and Ollama. It does not inject `.env` into containers or read
`DB_PASSWORD`/`JWT_SECRET_KEY` from it; those secrets are mounted as files and never
placed in the Compose environment. Compose may still use a local `.env` for its normal
non-secret variable interpolation, so keep production values in deployment-platform
configuration.

## Prerequisites

- Docker Engine with Compose v2.
- A host with enough memory for the configured Ollama text and vision models.
- TLS termination and a reverse proxy or deployment load balancer in front of port 8000.
- Two strong secret values supplied as files or by the deployment platform's secret
  manager.

## Create secrets

Create these local files outside version control for a single-host Compose deployment:

```text
secrets/db_password.txt
secrets/jwt_secret_key.txt
```

Each file must contain only its secret value and must not be empty. The `secrets/`
directory is ignored by Git. Restrict file access to the deployment account and never
copy these files into an image, CI artifact, or source archive.

For a managed platform, inject equivalent read-only files and set
`DB_PASSWORD_FILE_PATH` and `JWT_SECRET_KEY_FILE_PATH` to their host paths when invoking
Compose. The mounted container paths remain `/run/secrets/db_password` and
`/run/secrets/jwt_secret_key`.

## Configure and start

Set non-secret deployment values in the shell or platform configuration. At minimum,
replace the allowed origin with the HTTPS URL of the deployed frontend:

```powershell
$env:ALLOWED_ORIGINS = "https://food.example.com"
docker compose -f docker-compose.prod.yml config
docker compose -f docker-compose.prod.yml up --build -d
```

The production stack does not publish PostgreSQL or Ollama ports to the host. Only the
backend is published on port 8000; place it behind HTTPS and restrict direct network
access in the deployment environment.

Pull the configured models into the persistent Ollama volume after the first start:

```powershell
docker compose -f docker-compose.prod.yml exec ollama ollama pull qwen3:8b
docker compose -f docker-compose.prod.yml exec ollama ollama pull qwen3-vl:4b
```

## Verify and operate

```powershell
docker compose -f docker-compose.prod.yml ps
curl.exe --fail https://api.food.example.com/health/live
curl.exe --fail https://api.food.example.com/health/ready
```

Use `/health/live` for process liveness and `/health/ready` for deployment readiness.
Scrape `/metrics` from a private Prometheus network; do not expose operational metrics
publicly without authentication at the reverse proxy.

Back up the `postgres_data` volume regularly. The `ollama_models` volume can be rebuilt
by pulling models again, but backing it up can reduce recovery time. Apply database
migrations during startup only after testing them against a recent backup, and use
`docker compose -f docker-compose.prod.yml logs backend` to diagnose failed migrations.

To roll out a new application image on a single host:

```powershell
docker compose -f docker-compose.prod.yml build backend
docker compose -f docker-compose.prod.yml up -d backend
docker compose -f docker-compose.prod.yml ps
```
