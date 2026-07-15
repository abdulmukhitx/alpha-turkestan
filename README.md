# GeoAI TKO

GeoAI TKO is a FastAPI and React application for Sentinel-2 monitoring of the
Turkistan Region. It provides spectral-index maps, point and polygon analysis,
change comparison, transects, an experimental linear forecast, land-cover
classification, accounts, saved zones and reports in Russian, Kazakh and
English.

## Local development

1. Copy `.env.example` to `.env` and configure local paths.
2. Install backend dependencies with `python -m pip install -r backend/requirements.txt`.
3. Start the API with `uvicorn backend.main:app --reload --port 8000`.
4. In `frontend`, run `npm ci` and `npm run dev`.
5. Open `http://localhost:3000`.

Never commit `.env`, API keys, SMTP credentials, account databases, backups or
source raster files.

## Verification

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m compileall -q backend src tests
cd frontend
npm.cmd run build
npm.cmd audit --omit=dev
```

## Health checks

- `/healthz` is a minimal liveness probe.
- `/readyz` checks whether core raster and account dependencies can serve traffic.
- `/health` is the frontend-safe component summary. It intentionally excludes
  server filesystem paths and secrets.

## Field monitoring

Verified accounts can import and save AOIs on `/dashboard`, replay account
analyses from `/history`, and share durable map links. A monitoring run records
one observation for each zone and source-data version, evaluates the account's
threshold rules, deduplicates active alerts, resolves recovered conditions, and
uses the configured account email transport for delivery.

Set `MONITORING_SCHEDULER_ENABLED=true` on exactly one backend process to poll
the newest configured raster every `MONITORING_INTERVAL_SECONDS`. The scheduler
detects a replaced mosaic through its data version; bringing newly acquired
scenes into the mosaic/catalog remains an upstream ingestion responsibility.

## Data trust

The Sentinel layer now renders measured B04/B03/B02 true colour from the
selected application mosaic. Pixel, zone, period and time-series responses
include path-free evidence metadata: provider, product level, acquisition
window, contributing bands, processing description, data version and coverage.
The UI exposes that lineage in expandable evidence badges.

The current seven-band mosaics do **not** retain Sentinel-2's SCL scene-
classification layer. Nodata masking is applied, but cloud and cloud-shadow
screening cannot be independently verified and is labelled as limited in every
evidence record. Retain SCL and original scene identifiers in the future
ingestion manifest before treating observations as fully quality-screened.

## Deployment notes

The current SQLite, scheduler and in-process rate-limiter design supports one
backend process. Before running multiple workers or replicas, move accounts to
PostgreSQL, elect one monitoring scheduler, and rate-limit through a shared
Redis/reverse-proxy layer. Historical tile URLs include a source-data version
and may be cached by browsers or a CDN. See `docs/account-production.md` for
account backup and email configuration.

## Project direction

The planned release sequence is stability, Field Monitor pages, scheduled
monitoring and notifications, and then data-quality/provenance features. The
eight-year ML prediction release is intentionally deferred until the full
temporal dataset is available and validated.
