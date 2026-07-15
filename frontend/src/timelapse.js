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

const TILE_SIZE = 256

function clampLatitude(latitude) {
  return Math.max(-85.05112878, Math.min(85.05112878, latitude))
}

export function worldPixel(lon, lat, zoom) {
  const scale = (2 ** zoom) * TILE_SIZE
  const safeLon = Math.max(-180, Math.min(180, Number(lon)))
  const safeLat = clampLatitude(Number(lat))
  const radians = safeLat * Math.PI / 180
  return {
    x: ((safeLon + 180) / 360) * scale,
    y: ((1 - Math.asinh(Math.tan(radians)) / Math.PI) / 2) * scale,
  }
}

function polygonRings(geometry) {
  if (geometry?.type === 'Polygon') return geometry.coordinates || []
  if (geometry?.type === 'MultiPolygon') return (geometry.coordinates || []).flat()
  return []
}

export function geometryBounds(geometry) {
  const coordinates = polygonRings(geometry).flat()
  if (!coordinates.length) return null
  let west = Infinity
  let south = Infinity
  let east = -Infinity
  let north = -Infinity
  coordinates.forEach((position) => {
    const lon = Number(position?.[0])
    const lat = Number(position?.[1])
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) return
    west = Math.min(west, lon)
    south = Math.min(south, lat)
    east = Math.max(east, lon)
    north = Math.max(north, lat)
  })
  if (![west, south, east, north].every(Number.isFinite) || west >= east || south >= north) return null
  return [south, west, north, east]
}

export function uniqueSceneAcquisitions(scenes = [], limit = 12) {
  const acquisitions = new Map()
  scenes.forEach((scene) => {
    if (!scene?.scene_id || !scene?.acquired_at) return
    const key = `${String(scene.acquired_at).slice(0, 16)}|${scene.platform || ''}`
    const current = acquisitions.get(key)
    const cloud = Number.isFinite(Number(scene.cloud_cover)) ? Number(scene.cloud_cover) : Infinity
    const currentCloud = Number.isFinite(Number(current?.cloud_cover)) ? Number(current.cloud_cover) : Infinity
    if (!current || cloud < currentCloud) acquisitions.set(key, scene)
  })
  return [...acquisitions.values()]
    .sort((left, right) => String(left.acquired_at).localeCompare(String(right.acquired_at)))
    .slice(0, Math.max(1, limit))
}

export function padBounds(bounds, ratio = 0.08) {
  const [south, west, north, east] = bounds
  const latPadding = Math.max((north - south) * ratio, 0.00005)
  const lonPadding = Math.max((east - west) * ratio, 0.00005)
  return [
    clampLatitude(south - latPadding),
    Math.max(-180, west - lonPadding),
    clampLatitude(north + latPadding),
    Math.min(180, east + lonPadding),
  ]
}

export function tileGridForBounds(bounds, { minZoom = 5, maxZoom = 18, maxTiles = 16 } = {}) {
  const [south, west, north, east] = bounds
  for (let zoom = maxZoom; zoom >= minZoom; zoom -= 1) {
    const northwest = worldPixel(west, north, zoom)
    const southeast = worldPixel(east, south, zoom)
    const limit = (2 ** zoom) - 1
    const xMin = Math.max(0, Math.min(limit, Math.floor(northwest.x / TILE_SIZE)))
    const yMin = Math.max(0, Math.min(limit, Math.floor(northwest.y / TILE_SIZE)))
    const xMax = Math.max(0, Math.min(limit, Math.floor((southeast.x - 1e-7) / TILE_SIZE)))
    const yMax = Math.max(0, Math.min(limit, Math.floor((southeast.y - 1e-7) / TILE_SIZE)))
    const tileCount = (xMax - xMin + 1) * (yMax - yMin + 1)
    if (tileCount <= maxTiles || zoom === minZoom) {
      const tiles = []
      for (let y = yMin; y <= yMax; y += 1) {
        for (let x = xMin; x <= xMax; x += 1) tiles.push({ x, y, z: zoom })
      }
      return { zoom, xMin, xMax, yMin, yMax, tiles }
    }
  }
  throw new Error('Could not create a bounded timelapse tile grid')
}

function concreteTileUrl(template, tile) {
  return template
    .replace('{z}', String(tile.z))
    .replace('{x}', String(tile.x))
    .replace('{y}', String(tile.y))
}

async function fetchTile(url, signal) {
  const response = await fetch(url, { signal, cache: 'force-cache' })
  if (!response.ok) throw new Error(`Timelapse tile failed: ${response.status}`)
  const blob = await response.blob()
  if (typeof createImageBitmap === 'function') {
    const image = await createImageBitmap(blob)
    return { image, cleanup: () => image.close?.() }
  }
  const objectUrl = URL.createObjectURL(blob)
  const image = new Image()
  image.src = objectUrl
  await image.decode()
  return { image, cleanup: () => URL.revokeObjectURL(objectUrl) }
}

function canvasBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob)
      else reject(new Error('Could not encode the prepared timelapse frame'))
    }, 'image/webp', 0.9)
  })
}

function traceGeometry(context, geometry, zoom, offsetX, offsetY) {
  polygonRings(geometry).forEach((ring) => {
    ring.forEach((position, index) => {
      const pixel = worldPixel(position[0], position[1], zoom)
      const x = pixel.x - offsetX
      const y = pixel.y - offsetY
      if (index === 0) context.moveTo(x, y)
      else context.lineTo(x, y)
    })
    context.closePath()
  })
}

export async function prepareAoiFrame(template, geometry, { signal, onAssetLoaded } = {}) {
  const exactBounds = geometryBounds(geometry)
  if (!exactBounds) throw new Error('The selected AOI has no valid polygon bounds')
  const displayBounds = padBounds(exactBounds)
  const grid = tileGridForBounds(displayBounds)
  const decoded = new Array(grid.tiles.length)
  let cursor = 0

  async function worker() {
    while (cursor < grid.tiles.length) {
      const index = cursor
      cursor += 1
      const tile = grid.tiles[index]
      decoded[index] = await fetchTile(concreteTileUrl(template, tile), signal)
      onAssetLoaded?.()
    }
  }
  try {
    await Promise.all(Array.from({ length: Math.min(6, grid.tiles.length) }, worker))
  } catch (error) {
    decoded.filter(Boolean).forEach((entry) => entry.cleanup())
    throw error
  }

  const gridCanvas = document.createElement('canvas')
  gridCanvas.width = (grid.xMax - grid.xMin + 1) * TILE_SIZE
  gridCanvas.height = (grid.yMax - grid.yMin + 1) * TILE_SIZE
  const gridContext = gridCanvas.getContext('2d', { alpha: true })
  gridContext.imageSmoothingEnabled = true
  decoded.forEach((entry, index) => {
    const tile = grid.tiles[index]
    gridContext.drawImage(entry.image, (tile.x - grid.xMin) * TILE_SIZE, (tile.y - grid.yMin) * TILE_SIZE)
    entry.cleanup()
  })

  const northwest = worldPixel(displayBounds[1], displayBounds[2], grid.zoom)
  const southeast = worldPixel(displayBounds[3], displayBounds[0], grid.zoom)
  const gridOriginX = grid.xMin * TILE_SIZE
  const gridOriginY = grid.yMin * TILE_SIZE
  const cropLeft = Math.max(0, Math.floor(northwest.x - gridOriginX))
  const cropTop = Math.max(0, Math.floor(northwest.y - gridOriginY))
  const cropRight = Math.min(gridCanvas.width, Math.ceil(southeast.x - gridOriginX))
  const cropBottom = Math.min(gridCanvas.height, Math.ceil(southeast.y - gridOriginY))
  const output = document.createElement('canvas')
  output.width = Math.max(1, cropRight - cropLeft)
  output.height = Math.max(1, cropBottom - cropTop)
  const context = output.getContext('2d', { alpha: true })
  context.drawImage(gridCanvas, -cropLeft, -cropTop)
  context.globalCompositeOperation = 'destination-in'
  context.fillStyle = '#fff'
  context.beginPath()
  traceGeometry(context, geometry, grid.zoom, gridOriginX + cropLeft, gridOriginY + cropTop)
  context.fill('evenodd')
  context.globalCompositeOperation = 'source-over'

  const blob = await canvasBlob(output)
  const url = URL.createObjectURL(blob)
  const preparedImage = new Image()
  preparedImage.src = url
  try {
    await preparedImage.decode()
  } catch (error) {
    URL.revokeObjectURL(url)
    throw error
  }
  return { url, width: output.width, height: output.height, tileCount: grid.tiles.length, scope: 'aoi', decodedImage: preparedImage }
}

export async function prepareCenterFrame(template, center, { signal, onAssetLoaded } = {}) {
  const response = await fetch(previewTileUrl(template, center, 8), { signal, cache: 'force-cache' })
  if (!response.ok) throw new Error(`Timelapse preview failed: ${response.status}`)
  const url = URL.createObjectURL(await response.blob())
  const image = new Image()
  image.src = url
  try {
    await image.decode()
  } catch (error) {
    URL.revokeObjectURL(url)
    throw error
  }
  onAssetLoaded?.()
  return { url, width: image.naturalWidth, height: image.naturalHeight, tileCount: 1, scope: 'center', decodedImage: image }
}

export async function prepareSceneFrameBlob(blob, { onAssetLoaded } = {}) {
  const url = URL.createObjectURL(blob)
  const image = new Image()
  image.src = url
  try {
    await image.decode()
  } catch (error) {
    URL.revokeObjectURL(url)
    throw error
  }
  onAssetLoaded?.()
  return {
    url,
    width: image.naturalWidth,
    height: image.naturalHeight,
    tileCount: 1,
    scope: 'cdse',
    decodedImage: image,
  }
}

export function nextFrameIndex(currentIndex, frameCount, loop = true) {
  if (frameCount < 1) return -1
  if (currentIndex < 0) return 0
  if (currentIndex + 1 < frameCount) return currentIndex + 1
  return loop ? 0 : -1
}
