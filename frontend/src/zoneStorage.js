const ZONES_STORAGE_KEY = 'geoai-tko.saved-zones.v1'

function validPolygon(geometry) {
  return geometry?.type === 'Polygon'
    && Array.isArray(geometry.coordinates)
    && Array.isArray(geometry.coordinates[0])
    && geometry.coordinates[0].length >= 4
}

export function readSavedZones() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(ZONES_STORAGE_KEY) || '[]')
    if (!Array.isArray(parsed)) return []
    return parsed.filter((zone) => (
      zone && typeof zone.id === 'string' && typeof zone.name === 'string' && validPolygon(zone.geometry)
    ))
  } catch {
    return []
  }
}

export function writeSavedZones(zones) {
  window.localStorage.setItem(ZONES_STORAGE_KEY, JSON.stringify(zones))
}

export function clearSavedZones() {
  window.localStorage.removeItem(ZONES_STORAGE_KEY)
}

export function newZoneId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID()
  return `zone-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

export function cloneGeometry(geometry) {
  return JSON.parse(JSON.stringify(geometry))
}
