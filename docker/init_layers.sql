-- Day 3: Derived layers metadata table
-- Run: docker exec -i geoai-postgis psql -U geoai -d geoai_tko < init_layers.sql

CREATE TABLE IF NOT EXISTS derived_layers (
    id SERIAL PRIMARY KEY,
    layer_name TEXT NOT NULL UNIQUE,
    full_name TEXT,
    formula TEXT,
    value_min DOUBLE PRECISION,
    value_max DOUBLE PRECISION,
    value_mean DOUBLE PRECISION,
    crs TEXT,
    resolution_m INTEGER,
    source TEXT,
    acquisition_period TEXT,
    scenes INTEGER,
    composite_method TEXT,
    file_path TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- NDVI
INSERT INTO derived_layers (layer_name, full_name, formula, value_min, value_max, value_mean, crs, resolution_m, source, acquisition_period, scenes, composite_method, file_path)
VALUES (
    'ndvi',
    'Normalized Difference Vegetation Index',
    '(NIR - Red) / (NIR + Red) = (B8 - B4) / (B8 + B4)',
    -1.0, 1.0, 0.16,
    'EPSG:32642', 10,
    'COPERNICUS/S2_SR_HARMONIZED',
    '2023-06-01 to 2023-09-30',
    1632, 'median',
    'data/processed/ndvi_tko_final.tif'
)
ON CONFLICT (layer_name) DO NOTHING;

-- NDWI
INSERT INTO derived_layers (layer_name, full_name, formula, value_min, value_max, value_mean, crs, resolution_m, source, acquisition_period, scenes, composite_method, file_path)
VALUES (
    'ndwi',
    'Normalized Difference Water Index',
    '(Green - NIR) / (Green + NIR) = (B3 - B8) / (B3 + B8)',
    -0.96, 1.0, -0.26,
    'EPSG:32642', 10,
    'COPERNICUS/S2_SR_HARMONIZED',
    '2023-06-01 to 2023-09-30',
    1632, 'median',
    'data/processed/ndwi_tko_final.tif'
)
ON CONFLICT (layer_name) DO NOTHING;
