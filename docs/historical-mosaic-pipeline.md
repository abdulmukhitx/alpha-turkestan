# Unattended Sentinel-2 historical mosaics

`src/processing/historical_mosaic_pipeline.py` builds the five missing summer
periods (2018–2022) one by one. It uses the same CDSE source, seven bands,
physical-reflectance convention, EPSG:32641 grid, and 10 m resolution as the
QA-passed 2023–2025 mosaics.

The process is safe to disconnect from and restart. Completed JP2 files,
per-tile composites, merge blocks, final COGs, and QA-passed years are read
back and skipped. A systemd restart resumes from the last trustworthy
checkpoint.

## What it handles automatically

- Public STAC search with retry and pagination.
- Lowest-cloud candidate selection with at least two relative orbits in the
  top three whenever CDSE has them.
- Password-grant token refresh before the 30-minute expiry and after a 401.
- HTTP Range resume for `.part` downloads, exponential backoff, size checks,
  and three-window raster reads to catch truncated JP2 files.
- Replacement of a product that remains unavailable.
- Baseline-aware BOA conversion. Asset `raster:bands` scale/offset metadata is
  authoritative; otherwise the product ID baseline is used (`N0400` or newer
  means `(DN - 1000) / 10000`).
- Block-wise reprojection and compositing with bounded RAM. Real pixel
  footprints are measured after the build; additional products are downloaded
  automatically when a tile still has too much nodata.
- Resumable mosaic merge and strict translation through GDAL's COG driver.
- Full read-back QA inside the official Turkestan boundary: structure,
  overviews, nodata coverage, finite/range checks for all bands, vegetation
  B08 sanity check, preview, provenance manifest, and final metadata.

The process cannot repair wrong credentials, MFA on the CDSE account, a full
disk, or a year with no source imagery. Those conditions are recorded in
`pipeline_state.json`; systemd retries after the external condition is fixed.

## Server configuration

Use a large local data volume. Add these lines to the production `.env`:

```dotenv
S2_DATA_ROOT=/srv/geoai-tko-data
MOSAICS_DIR=/srv/geoai-tko-data/mosaics
CDSE_USERNAME=your-cdse-account@example.com
CDSE_PASSWORD=your-password
```

The CDSE account must not have MFA because direct Zipper/OData downloads use
the `cdse-public` password grant. Do not use `CDSE_CLIENT_ID` and
`CDSE_CLIENT_SECRET` for this job; those credentials serve the small-AOI
Process API and produce the wrong token audience for direct JP2 downloads.

Before starting, confirm space and credentials:

```bash
cd ~/geoai-tko/app
df -h /srv/geoai-tko-data
set -a
source .env
set +a
../.venv313/bin/python src/processing/historical_mosaic_pipeline.py --help
```

The established 2023 build suggests roughly 50–60 GB raw, 65–75 GB tile
composites, 65–75 GB staging, and 65–75 GB final COG per year. Because the job
removes staging and the service removes derived tile composites only after QA,
plan for about 650–750 GB for all five final COGs plus retained raw JP2s. Add
`--cleanup-raw-after-qa` only when space is tighter and automatic deletion of
QA-approved raw sources is desired; CDSE can redownload them later.

## Install and start the user service

```bash
mkdir -p ~/.config/systemd/user
cp deploy/ubuntu/geoai-s2-history.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now geoai-s2-history.service
loginctl enable-linger "$USER"
```

`enable-linger` keeps the user service running after SSH logout and across a
server reboot. Follow progress with:

```bash
journalctl --user -u geoai-s2-history.service -f
systemctl --user status geoai-s2-history.service
find /srv/geoai-tko-data/mosaics -name pipeline_state.json -print -exec cat {} \;
```

Stopping and starting is safe:

```bash
systemctl --user stop geoai-s2-history.service
systemctl --user start geoai-s2-history.service
```

Each completed period has:

```text
/srv/geoai-tko-data/mosaics/2018_summer/
  s2_mosaic_cog.tif
  preview.png
  qa_report.json
  metadata.json
  pipeline_state.json
```

The backend already registers 2018–2022. It reports a period as unavailable
until that year's `s2_mosaic_cog.tif` exists, so partial processing is never
shown as usable data. Restart the backend after new years finish so long-lived
raster readers and UI metadata start from a clean state.
