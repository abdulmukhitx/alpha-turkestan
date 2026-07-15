export function lonLatToTile(center, zoom = 8) {
  const lat = Math.max(-85.05112878, Math.min(85.05112878, Number(center?.[0]) || 0))
  const lon = Math.max(-180, Math.min(180, Number(center?.[1]) || 0))
  const scale = 2 ** zoom
  const x = Math.floor(((lon + 180) / 360) * scale)
  const latitudeRadians = lat * Math.PI / 180
  const y = Math.floor((1 - Math.asinh(Math.tan(latitudeRadians)) / Math.PI) / 2 * scale)
  return { x, y, z: zoom }
}

export function previewTileUrl(template, center, zoom = 8) {
  const { x, y, z } = lonLatToTile(center, zoom)
  return template
    .replace('{z}', String(z))
    .replace('{x}', String(x))
    .replace('{y}', String(y))
}

export function nextFrameIndex(currentIndex, frameCount, loop = true) {
  if (frameCount < 1) return -1
  if (currentIndex < 0) return 0
  if (currentIndex + 1 < frameCount) return currentIndex + 1
  return loop ? 0 : -1
}
