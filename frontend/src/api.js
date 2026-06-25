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

export async function fetchPixel(lat, lon) {
  const r = await fetch(`/api/pixel?lat=${lat.toFixed(6)}&lon=${lon.toFixed(6)}`)
  if (!r.ok) throw new Error(`Pixel fetch failed: ${r.status}`)
  return r.json()
}

export async function fetchAnalysis({ lat, lon, ndvi, ndwi, ndre, ndmi, bsi, ml_class, ml_class_ru, ml_confidence }) {
  const r = await fetch('/api/analyze', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ lat, lon, ndvi, ndwi, ndre, ndmi, bsi, ml_class, ml_class_ru, ml_confidence }),
  })
  if (!r.ok) throw new Error(`Analysis failed: ${r.status}`)
  return r.json()
}

/** Leaflet-compatible XYZ tile URL template for a given layer. */
export const tileUrl = (layer) => `/tiles/${layer}/{z}/{x}/{y}.png`
