/* All requests go through the Vite dev proxy → http://localhost:8000 */

export async function fetchHealth() {
  const r = await fetch('/health')
  if (!r.ok) throw new Error(`Health fetch failed: ${r.status}`)
  return r.json()
}

export async function fetchMetadata() {
  const r = await fetch('/metadata')
  if (!r.ok) throw new Error(`Metadata fetch failed: ${r.status}`)
  return r.json()
}

export async function fetchPeriods() {
  const r = await fetch('/api/periods')
  if (!r.ok) throw new Error(`Periods fetch failed: ${r.status}`)
  return r.json()
}

export async function fetchPixel(lat, lon, period) {
  const r = await fetch(`/api/pixel?lat=${lat.toFixed(6)}&lon=${lon.toFixed(6)}&period=${period}`)
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Pixel fetch failed: ${r.status}`)
  }
  return r.json()
}

export async function fetchAnalysis({ lat, lon, period, ndvi, ndwi, ndre, ndmi, bsi, savi, nbr, ml_class, ml_class_ru, ml_confidence }) {
  const r = await fetch('/api/analyze', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ lat, lon, period, ndvi, ndwi, ndre, ndmi, bsi, savi, nbr, ml_class, ml_class_ru, ml_confidence }),
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Analysis failed: ${r.status}`)
  }
  return r.json()
}

/** Leaflet-compatible XYZ tile URL template for a given layer + period. */
export const tileUrl = (layer, period) => `/tiles/${layer}/{z}/{x}/{y}.png?period=${period}`

/** Experimental all-years linear-trend forecast tile URL. */
export const forecastTileUrl = (index, targetYear) =>
  `/tiles/forecast/${index}/${targetYear}/{z}/{x}/{y}.png`

export async function fetchPointForecast(lat, lon, index, targetYear) {
  const params = new URLSearchParams({
    lat: lat.toFixed(6),
    lon: lon.toFixed(6),
    index,
    target_year: String(targetYear),
  })
  const r = await fetch(`/api/forecast/point?${params}`)
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Forecast failed: ${r.status}`)
  }
  return r.json()
}

export async function fetchZoneStats(geometry, period) {
  const r = await fetch('/api/zone_stats', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ geometry, period }),
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Zone stats failed: ${r.status}`)
  }
  return r.json()
}

export async function fetchTransect(geometry, layer, period) {
  const r = await fetch('/api/transect', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ geometry, layer, period }),
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Transect failed: ${r.status}`)
  }
  return r.json()
}

/** Leaflet-compatible XYZ tile URL template for a change-detection layer. */
export const changeTileUrl = (index, periodBefore, periodAfter) =>
  `/tiles/change/${index}/{z}/{x}/{y}.png?period_before=${periodBefore}&period_after=${periodAfter}`

export async function fetchChangeStats(geometry, periodBefore, periodAfter) {
  const r = await fetch('/api/change_stats', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ geometry, period_before: periodBefore, period_after: periodAfter }),
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Change stats failed: ${r.status}`)
  }
  return r.json()
}

export async function fetchZoneReport({ geometry, zoneStats, activeLayer, period }) {
  const r = await fetch('/api/zone_report', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({
      geometry,
      zone_stats:       zoneStats,
      active_layer:     activeLayer,
      period,
    }),
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Zone report failed: ${r.status}`)
  }
  return r.json()
}
