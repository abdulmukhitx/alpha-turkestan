# GeoAI TKO

GeoAI TKO is a FastAPI and React application for Sentinel-2 monitoring of the
Turkistan Region. It provides spectral-index maps, point and polygon analysis,
change comparison, transects, an experimental linear forecast, land-cover
classification, accounts, saved zones and reports in Russian, Kazakh and
English. Saved AOIs also act as operational work sites: users can turn a field
or monitoring alert into an assigned investigation and carry it through to a
documented outcome.

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

The `/work` page is the action layer above monitoring. A verified user can
create a field task from any saved AOI or directly from an alert, set its type,
priority, responsible person and due date, and then record:

- the investigation context and original monitoring trigger;
- field observations and chronological logbook entries;
- findings, the action taken and the verified outcome;
- progress through `open`, `in_progress`, `waiting` and `closed` states.

The workbench is also a ground-truth instrument for the land-cover model. A
field operator records an observed land-cover class, confidence, GPS position
and observation time. The backend samples the selected Sentinel-2 mosaic at
that point and stores the spectral indices, ML class, confidence and evidence
version beside the field label. The resulting validation dataset reports model
agreement and conflicts and exports as CSV or point GeoJSON for QGIS, accuracy
review or a future retraining pipeline. Samples outside the task AOI or from a
different image year remain auditable but are excluded from agreement metrics.

Closing a task requires an outcome. The task snapshots its AOI name and geometry
when it is created, so account export retains the investigation boundary even
if the original saved zone is later removed. A print stylesheet turns the task
workbench into a clean browser/PDF field report.

Field-work API contracts:

- `GET/POST /api/account/cases` lists or creates account-owned field tasks.
- `GET/PATCH/DELETE /api/account/cases/{id}` reads or changes one task.
- `POST /api/account/cases/{id}/updates` appends an auditable logbook entry.
- `POST /api/account/cases/{id}/validations` captures a server-sampled
  satellite-to-ground comparison.
- `GET /api/account/ground-truth` returns the account validation dataset and
  model-agreement summary.

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

## Timelapse and scene discovery

The map timelapse plays the locally available annual summer mosaics and can be
opened as a larger studio with frame selection, speed, looping and transition
controls. The studio's **Scene catalogue** tab performs a bounded search of the
public Copernicus Data Space STAC `sentinel-2-l2a` collection for the current
region, date range and maximum product cloud cover.

When a polygon AOI is active, the studio selects a bounded high-resolution tile
grid, downloads and decodes every selected annual frame, composites the tiles,
and masks the result to the polygon before enabling playback. Prepared WebP
frames remain in memory for the player and use a short 160 ms crossfade, so no
network request or image decode is performed during frame changes. The grid is
limited to 16 tiles per period and six concurrent downloads.

Catalogue scenes are intentionally metadata-only and carry `renderable: false`.
Product cloud cover describes the full Sentinel source tile, not cloud cover
inside the selected AOI. Scene rendering/export will require a separate CDSE
Process API OAuth integration and retained quality masks; until that is added,
the player continues to use the verified local mosaics. Configure discovery
with `CDSE_CATALOG_ENABLED`, `CDSE_CATALOG_TIMEOUT_SECONDS`,
`CDSE_CATALOG_CACHE_SECONDS` and `MAX_CONCURRENT_CATALOG_SEARCHES`. No CDSE
credential is required for this public catalogue stage.

API contracts:

- `GET /api/timelapse/capabilities` reports discovery and rendering support.
- `POST /api/timelapse/scenes` accepts a WGS84 bounding box, date range, cloud
  threshold and a bounded result limit.

## Deployment notes

The current SQLite, scheduler and in-process rate-limiter design supports one
backend process. Before running multiple workers or replicas, move accounts to
PostgreSQL, elect one monitoring scheduler, and rate-limit through a shared
Redis/reverse-proxy layer. Historical tile URLs include a source-data version
and may be cached by browsers or a CDN. See `docs/account-production.md` for
account backup and email configuration.

For the Vercel frontend project, set **Root Directory** to `frontend`. The
included `frontend/vercel.json` rewrites direct SPA routes such as `/map`,
`/dashboard`, `/work` and `/history` to `index.html`, allowing React Router to handle
refreshes and shared links. Commit the file and redeploy after changing it.

## Project direction

The platform now covers two operational loops: observe, investigate, act and
report; and sample, compare, review and export ground truth. The next product
increments are geotagged photo attachments/offline field capture, team
membership and real assignees, stratified sampling-point generation, per-AOI
monitoring plans, and automatic follow-up checks against new scenes. The
eight-year ML prediction release remains intentionally deferred until the full
temporal dataset is available and validated.
