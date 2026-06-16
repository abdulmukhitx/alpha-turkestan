-- GeoAI-TKO: PostGIS initialization
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_raster;

-- Sentinel-2 raw imagery metadata table
CREATE TABLE IF NOT EXISTS sentinel_metadata (
    id SERIAL PRIMARY KEY,
    product_id TEXT UNIQUE NOT NULL,
    sensing_date DATE NOT NULL,
    cloud_cover_pct DOUBLE PRECISION,
    bbox geometry(Polygon, 4326),
    bands TEXT[],
    file_path TEXT,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);

-- Raster tile index (for future tiling)
CREATE TABLE IF NOT EXISTS raster_index (
    id SERIAL PRIMARY KEY,
    tile_name TEXT NOT NULL,
    bounds geometry(Polygon, 32640),
    resolution_m DOUBLE PRECISION,
    file_path TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Spatial index
CREATE INDEX IF NOT EXISTS idx_sentinel_bbox ON sentinel_metadata USING GIST (bbox);
CREATE INDEX IF NOT EXISTS idx_raster_bounds ON raster_index USING GIST (bounds);

-- Log table for ingestion runs
CREATE TABLE IF NOT EXISTS ingestion_log (
    id SERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'started',
    file_count INTEGER DEFAULT 0,
    details JSONB,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
