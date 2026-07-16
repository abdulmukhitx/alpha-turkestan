# GeoAI TKO: project context for Claude

Read this document before proposing or implementing a redesign. It explains the product, domain, runtime behavior, evidence model, and UI contracts that the design must preserve. Never copy values from `.env` into code, documentation, chat, or screenshots.

## What this product is

GeoAI TKO is a map-first remote-sensing and land-monitoring web application for the Turkestan Region of Kazakhstan. It turns multi-year Sentinel-2 satellite mosaics into visual layers and practical analyses for agronomists, land managers, GIS/remote-sensing analysts, and regional decision-makers.

This is not a generic analytics dashboard. The map is the primary work surface. Users choose a period and spectral layer, inspect locations, draw real geometries, compare years, measure change, and export results. Supporting controls and results should help the map rather than compete with it.

The current product name is `GeoAI TKO`. The interface is localized in Russian, Kazakh, and English. Russian is the default language, and Kazakhstan-specific copy and the `Asia/Qyzylorda` time zone are intentional.

## Product promise

The product helps a user answer questions such as:

- What vegetation, water, moisture, exposed-soil, or degradation signal is visible at this location?
- What are the mean, spread, and land-cover composition inside a field or custom zone?
- How does a selected index vary along a line?
- How did a zone or the map change between two summer periods?
- What does a simple continuation of the 2023-2025 trend look like for a future year?
- Can I save a zone, return to it later, synchronize it across devices, and export a report?

The interface should feel credible, calm, precise, and operational: closer to a high-quality GIS/earth-observation workspace than a marketing landing page or a science-fiction control panel.

## Data and domain model

The runtime uses summer Sentinel-2 L2A mosaics for 2023, 2024, and 2025. The mosaics are Cloud Optimized GeoTIFFs (COGs), built from Copernicus Data Space Ecosystem data and stored as physical reflectance. They contain seven bands:

- B02 blue
- B03 green
- B04 red
- B05 red edge
- B08 near infrared
- B8A narrow near infrared
- B11 short-wave infrared

The mosaics use EPSG:32641 and approximately 10 m pixels. Backend tile endpoints calculate index imagery dynamically and return transparent PNG tiles. Data is clipped to the Turkestan Region boundary while the basemap remains visible outside it.

The seven analytic indices are:

| Index | Formula | Primary interpretation |
|---|---|---|
| NDVI | `(B08-B04)/(B08+B04)` | vegetation activity/density |
| NDWI | `(B03-B08)/(B03+B08)` | water signal |
| NDRE | `(B08-B05)/(B08+B05)` | red-edge vegetation condition/stress |
| NDMI | `(B8A-B11)/(B8A+B11)` | moisture condition |
| BSI | `((B11+B04)-(B08+B02))/((B11+B04)+(B08+B02))` | exposed/bare soil |
| SAVI | `1.5*(B08-B04)/(B08+B04+0.5)` | vegetation adjusted for soil brightness |
| NBR | `(B08-B11)/(B08+B11)` | burn/degradation-related signal |

The color stretches in `backend/main.py` are measured from this region's actual imagery, not generic textbook ranges. Do not casually replace their visual meaning. BSI also has the opposite improvement/degradation sign from the other indices: increasing bare-soil signal is treated as degradation.

Land cover has six classes: agriculture, dense vegetation, sparse vegetation, bare soil, urban, and water. A legacy Random Forest is used for bulk zone classification; a 13-feature XGBoost model using indices, B08, and 3x3 texture features is used for point and change analysis when its model file is available. ML output is optional, so the UI must also work when classification is unavailable.

The `Satellite image` selection in the current single-map UI is technically a virtual layer: it hides the analytic index overlay and exposes the selected external basemap. It is not a natural-color Sentinel tile endpoint. Do not silently change that behavior during a visual-only redesign; treat any copy or product correction as a separate decision.

## Four workspace modes

Only one major mode is active at a time.

### 1. Overview

The normal exploration mode. The user selects a global period and an index layer, then can:

- click the map for a point reading;
- draw a polygon for zone statistics and a time series;
- draw a line for a transect/profile;
- save, reopen, rename, edit, or delete zones;
- adjust overlay opacity and read the legend;
- switch basemap and toggle labels.

Clicking a point first loads measured index values and optional ML land-cover classification. It then asks an AI service for a short interpretation. The results panel opens automatically.

Drawing a polygon computes area, pixel count, statistics for all indices, land-cover proportions, and a multi-year time series. Users can define threshold rules and export the zone as PDF, CSV, or GeoJSON. The PDF contains a map capture, statistics, land-cover information, and an AI-generated or local-fallback narrative.

Drawing a line samples the selected index along the path and displays a Recharts transect chart plus summary values. A transect is unavailable when the virtual satellite layer is selected.

### 2. Compare

This is a synchronized split-screen map implemented with MapLibre GL. Each side has its own period and index. A draggable divider reveals more or less of each side. The two maps keep navigation synchronized, and clicks/drawing retain which pane supplied the context.

The split view intentionally excludes the virtual satellite choice because comparison is period-specific and only the seven analytic layers are backed by period tiles. Its left-side and right-side layer selectors replace the normal global layer panel.

### 3. Change

The user chooses a `before` period, an `after` period, and an index in a contextual bottom bar. A signed red-to-neutral-to-green change overlay sits over the normal map. The user draws a polygon to calculate:

- before, after, mean delta, variability, direction, and significantly changed area for every index;
- land-cover area before and after;
- the strongest class transitions;
- an optional AI interpretation of the change.

The periods must differ. Change mode uses a dedicated polygon intent, even though it reuses the normal map drawing plumbing.

### 4. Forecast

This is explicitly an experimental, low-confidence scenario, not a trained predictive ML model. It uses ordinary least squares across the available annual observations (currently 2023-2025) and can project up to the configured maximum horizon, currently five years beyond the latest source year.

The map shows a forecast raster for the selected index and target year. Clicking a point shows observed values, projected value, annual slope, direction, fit quality, R-squared/RMSE, and a sensitivity envelope. The UI must continue to label this as a trend-continuation prototype and must not imply certainty.

## Evidence and trust model

The application contains four different evidence levels. Good design must make them legible and must never blend them into one undifferentiated `AI result`:

1. **Measured/derived satellite data:** band-derived indices, area, pixel counts, zonal statistics, and transects.
2. **ML classification:** predicted land-cover class, probability/confidence, and class transitions.
3. **AI narrative:** Groq or DeepSeek explains already-computed results; it does not calculate the satellite metrics. A deterministic local text fallback exists.
4. **Experimental scenario:** the forecast is a three-year linear extrapolation with a sensitivity range, not a validated future observation.

Labels, hierarchy, badges, explanatory copy, and uncertainty treatment should communicate these distinctions. Data values should be visually primary; AI prose should remain supporting interpretation.

## Screen anatomy and UI contract

The established information architecture is intentional:

- `TopBar`: product identity, pointer coordinates, global period, language, service health, refresh, and account entry.
- `WorkspaceNav`: the four major modes plus layer-panel and results-panel toggles.
- `LayerPanel` on the left: map inputs, layer selection, zone/profile tools, saved zones, opacity, and legend.
- Center map: always receives the remaining space and should remain the dominant surface.
- `AnalysisPanel` on the right: contextual results for points, zones, transects, change, or forecast. It starts closed and opens when work produces a result.
- Contextual bottom bars: configuration for Change and Forecast only. Controls that are irrelevant to the active mode should stay hidden.
- Account/profile dialog: authentication, verification, preferences, security, sessions, saved-analysis history, data export, and deletion.

Do not solve visual density by turning the map into a small fixed card. At widths below roughly 1100 px, panels overlay the map instead of compressing it. At narrower breakpoints, mode and panel controls become icon-forward and horizontally compact.

## Persistence and accounts

Guests can use the map and save zones locally in browser `localStorage`. After sign-in, verified accounts store zones and analyses in the backend SQLite database. When a guest signs in with existing local zones, the product offers to migrate them to cloud storage.

Personal accounts support:

- password registration/login and Google Identity Services login/linking;
- an additional app email-verification step before cloud storage is enabled;
- password recovery and password changes;
- 30-day sessions and per-device session revocation;
- persisted language, time zone, layer, basemap, period, opacity, and panel preferences;
- synchronized threshold-alert rules;
- saved zones and saved analysis snapshots;
- operational field cases with structured satellite-to-ground validation samples;
- account export and permanent deletion.

The `/work` ground-truth workflow is part of the evidence model, not a generic
CRM. A validation record pairs a field-observed land-cover class, GPS position,
timestamp and observer confidence with a server-sampled Sentinel-2 pixel, its
indices, optional ML class/confidence and source data version. Only samples
inside the snapshotted case AOI and from the same image year count toward model
agreement; excluded samples remain in the audit trail. CSV and point-GeoJSON
exports are intended for GIS review and later training/validation work.

The current SQLite account store assumes one backend instance. This is a personal-account product, not an organization/roles product.

## Runtime architecture

### Frontend

- React 18 and Vite; there is no page router.
- `frontend/src/App.jsx` is the main orchestrator and owns most mode, geometry, request, account, and panel state.
- `frontend/src/components/MapView.jsx` is the normal Leaflet map.
- `frontend/src/components/SplitMapView.jsx` is the MapLibre comparison view.
- `frontend/src/components/WorkspaceNav.jsx`, `TopBar.jsx`, `LayerPanel.jsx`, and `AnalysisPanel.jsx` define the main shell.
- Result-specific components include `ZoneStatsPanel`, `ZoneTimeSeries`, `TransectChart`, `ChangeStatsPanel`, `ForecastPanel`, and `ZoneReport`.
- `frontend/src/api.js` is the browser API boundary.
- `frontend/src/i18n.jsx` contains Russian, Kazakh, and English dictionaries and locale formatting.
- `frontend/src/index.css` contains shared tokens, the full shell, component styling, and responsive rules.
- Recharts renders time-series and transect visuals. jsPDF and html2canvas build downloadable reports and are loaded lazily.

The normal map uses remote Esri/CARTO basemaps and CARTO labels. The comparison map uses MapLibre because it needs synchronized canvases and a draggable comparison divider. A redesign must account for both engines and should not assume every map control is a React-only visual layer.

### Backend

- FastAPI v4 in `backend/main.py`.
- Rasterio, rio-tiler, NumPy, Pillow, and PyProj handle COG reading, reprojection, index computation, masks, tiles, polygons, and transects.
- scikit-learn/XGBoost model bundles provide optional land-cover classification.
- Groq and DeepSeek are called through OpenAI-compatible APIs for narrative interpretation; local fallbacks keep core analysis usable without them.
- Account routes are in `backend/account_api.py`, persistence in `backend/account_store.py`, mail in `backend/account_mailer.py`, and backup tooling in `backend/backup_accounts.py`.
- The API validates geometry size, request size, period IDs, analysis pixel counts, and concurrent expensive analyses.

Important endpoint families are:

- `/health`, `/metadata`, `/api/periods`
- `/tiles/{layer}/...`, `/tiles/change/{index}/...`, `/tiles/forecast/{index}/{year}/...`
- `/api/pixel`, `/api/analyze`, `/api/forecast/point`
- `/api/zone_stats`, `/api/zone_timeseries`, `/api/transect`
- `/api/change_stats`, `/api/change_overview`, `/api/zone_report`
- `/api/account/...` for identity, preferences, sessions, zones, analyses, export, and deletion

### Offline processing

`src/processing/` is the data-engineering pipeline, not browser runtime code. It contains scripts to discover/download Sentinel-2 scenes, select composite candidates, reproject and fill gaps, build/validate COG mosaics, calculate indices, extract training samples, train Random Forest/XGBoost classifiers, and run QA investigations. The very large raster and model artifacts live outside Git and are configured with environment paths.

## Design goals for a future redesign

When the user asks for a redesign, aim for:

- a professional earth-observation/GIS workspace with strong information hierarchy;
- immediate clarity about the active mode, active time period, active index, and current geometry/tool state;
- a map that remains visually dominant;
- dense expert information that still scans quickly;
- clear differentiation among measured data, ML, AI narrative, and forecast uncertainty;
- restrained, purposeful motion and polished loading/empty/error states;
- better small-screen ergonomics without hiding core workflows;
- consistent iconography instead of emoji or unrelated icon styles;
- readable charts, legends, values, confidence, and units;
- visual quality suitable for agronomy, government, and environmental monitoring rather than a generic SaaS template.

The current navy/cyan theme is an implementation, not an immutable brand. It may be refined or replaced when requested, but satellite imagery and analytic color ramps must remain readable. Spectral ramps are data colors and should not be reused as navigation-state colors. Success/online, selection/focus, warning, degradation, and improvement need distinct semantics beyond color alone.

Avoid decorative glass effects, excessive glow, tiny low-contrast text, permanently visible controls for inactive workflows, and cards nested inside cards without a clear hierarchy.

## Functional and accessibility constraints

A visual redesign must not accidentally change calculations, API contracts, geometry behavior, account security, or persistence. In particular:

- keep mode exclusivity and contextual controls;
- preserve automatic result-panel opening after analysis;
- preserve saved-zone guest/cloud behavior and migration;
- preserve localization and allow longer Russian/Kazakh labels;
- preserve legends, units, data precision, confidence, warnings, and experimental labels;
- preserve keyboard focus visibility and meaningful accessible names;
- expose pressed/expanded state with `aria-pressed` and `aria-expanded` where applicable;
- do not rely on color alone for active, improvement, degradation, success, or error states;
- respect reduced-motion preferences;
- keep useful pointer targets and test at 1280 px, 900 px, and a narrow mobile viewport;
- keep data/table/download alternatives for results where available.

## Key files to inspect before editing design

1. `frontend/UI_DESIGN_NOTES.md` for the existing layout and accessibility contract.
2. `frontend/src/App.jsx` for state transitions and how the shell is composed.
3. `frontend/src/index.css` for tokens, all current states, and breakpoints.
4. `frontend/src/components/WorkspaceNav.jsx`, `TopBar.jsx`, `LayerPanel.jsx`, and `AnalysisPanel.jsx` for primary hierarchy.
5. `frontend/src/components/MapView.jsx` and `SplitMapView.jsx` for map controls, geometry, clipping, and comparison behavior.
6. Result components and `AccountDialog.jsx` so the redesign covers real content, not only the empty map state.
7. `frontend/src/i18n.jsx` so proposed layouts survive all three languages.
8. `backend/main.py` and `frontend/src/api.js` only as needed to understand result shapes; do not move domain calculations into the frontend.

## Local development and verification

Backend from the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000
```

Frontend:

```powershell
cd frontend
npm run dev
```

The Vite app runs on port 3000 and proxies API/tile requests to port 8000. Useful verification commands are:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
cd frontend
npm run build
```

Real COGs and classifier bundles may be unavailable on another machine; the interface and failure/disabled states still need to remain coherent.

## How to begin a design task

If the user asks you to understand the project before changing it, do not edit immediately. First inspect the files above and respond with:

1. a concise description of the product and users;
2. the four modes and three core analysis interactions (point, polygon, line);
3. the evidence/trust hierarchy;
4. the current screen anatomy and responsive behavior;
5. the design opportunities and risks you see;
6. any design-direction choice that genuinely requires user input.

When implementation is authorized, preserve the behavior first, update shared tokens/components coherently, cover meaningful empty/loading/error/data states, and verify both the build and representative viewport layouts.
