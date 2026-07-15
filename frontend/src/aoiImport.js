const MAX_IMPORTED_VERTICES = 10_000

function finiteCoordinate(value, axis) {
  if (!Array.isArray(value) || value.length < 2) throw new Error('Every position must contain longitude and latitude')
  const lon = Number(value[0])
  const lat = Number(value[1])
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) throw new Error('Coordinates must be finite numbers')
  if (lon < -180 || lon > 180 || lat < -90 || lat > 90) throw new Error(`Invalid ${axis} coordinate`)
  return [lon, lat]
}

function normalizeRing(ring) {
  if (!Array.isArray(ring) || ring.length < 3) throw new Error('A polygon ring needs at least three positions')
  const normalized = ring.map((position) => finiteCoordinate(position, 'polygon'))
  const first = normalized[0]
  const last = normalized[normalized.length - 1]
  if (first[0] !== last[0] || first[1] !== last[1]) normalized.push([...first])
  if (normalized.length < 4) throw new Error('A polygon ring needs at least four closed positions')
  return normalized
}

export function normalizePolygon(geometry) {
  if (!geometry || geometry.type !== 'Polygon' || !Array.isArray(geometry.coordinates)) {
    throw new Error('Only Polygon and MultiPolygon AOIs are supported')
  }
  const coordinates = geometry.coordinates.map(normalizeRing)
  const vertices = coordinates.reduce((total, ring) => total + ring.length, 0)
  if (vertices > MAX_IMPORTED_VERTICES) throw new Error(`AOI exceeds ${MAX_IMPORTED_VERTICES} vertices`)
  return { type: 'Polygon', coordinates }
}

function featureName(feature, fallback, index) {
  const candidate = feature?.properties?.name || feature?.properties?.title || feature?.id
  const cleaned = String(candidate || '').trim()
  return cleaned.slice(0, 80) || `${fallback} ${index + 1}`
}

function polygonEntries(geometry, name) {
  if (geometry?.type === 'Polygon') return [{ name, geometry: normalizePolygon(geometry) }]
  if (geometry?.type === 'MultiPolygon') {
    return geometry.coordinates.map((coordinates, index) => ({
      name: geometry.coordinates.length > 1 ? `${name} ${index + 1}` : name,
      geometry: normalizePolygon({ type: 'Polygon', coordinates }),
    }))
  }
  throw new Error('Only Polygon and MultiPolygon AOIs are supported')
}

export function parseGeoJsonAois(text, fallbackName = 'Imported AOI') {
  let document
  try {
    document = JSON.parse(text)
  } catch {
    throw new Error('The GeoJSON file is not valid JSON')
  }

  const features = document.type === 'FeatureCollection'
    ? document.features
    : document.type === 'Feature'
      ? [document]
      : [{ type: 'Feature', properties: {}, geometry: document }]
  if (!Array.isArray(features) || features.length === 0) throw new Error('The GeoJSON file has no features')

  return features.flatMap((feature, index) => (
    polygonEntries(feature.geometry, featureName(feature, fallbackName, index))
  ))
}

function coordinatesFromKml(value) {
  const coordinates = value.trim().split(/\s+/).filter(Boolean).map((tuple) => {
    const [lon, lat] = tuple.split(',')
    return [Number(lon), Number(lat)]
  })
  return normalizeRing(coordinates)
}

export function parseKmlAois(text, fallbackName = 'Imported AOI') {
  const placemarks = [...text.matchAll(/<Placemark\b[^>]*>([\s\S]*?)<\/Placemark>/gi)]
  const containers = placemarks.length ? placemarks.map((match) => match[1]) : [text]
  const results = []
  containers.forEach((container, containerIndex) => {
    const nameMatch = container.match(/<name\b[^>]*>([\s\S]*?)<\/name>/i)
    const baseName = nameMatch?.[1]?.replace(/<[^>]+>/g, '').trim() || `${fallbackName} ${containerIndex + 1}`
    const polygons = [...container.matchAll(/<Polygon\b[^>]*>([\s\S]*?)<\/Polygon>/gi)]
    polygons.forEach((polygonMatch, polygonIndex) => {
      const coordinateBlocks = [...polygonMatch[1].matchAll(/<coordinates\b[^>]*>([\s\S]*?)<\/coordinates>/gi)]
      if (!coordinateBlocks.length) return
      const geometry = normalizePolygon({
        type: 'Polygon',
        coordinates: coordinateBlocks.map((match) => coordinatesFromKml(match[1])),
      })
      results.push({
        name: polygons.length > 1 ? `${baseName} ${polygonIndex + 1}`.slice(0, 80) : baseName.slice(0, 80),
        geometry,
      })
    })
  })
  if (!results.length) throw new Error('The KML file contains no polygon AOIs')
  return results
}

function tokenizeWkt(text) {
  const tokens = text.match(/[(),]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/g)
  if (!tokens) throw new Error('Invalid WKT geometry')
  return tokens
}

function parseWktGroup(tokens, state) {
  if (tokens[state.index++] !== '(') throw new Error('Invalid WKT parentheses')
  const result = []
  if (tokens[state.index] === '(') {
    while (tokens[state.index] === '(') {
      result.push(parseWktGroup(tokens, state))
      if (tokens[state.index] === ',') state.index += 1
    }
  } else {
    while (tokens[state.index] !== ')' && state.index < tokens.length) {
      const lon = Number(tokens[state.index++])
      const lat = Number(tokens[state.index++])
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) throw new Error('Invalid WKT coordinate')
      result.push([lon, lat])
      if (tokens[state.index] === ',') state.index += 1
    }
  }
  if (tokens[state.index++] !== ')') throw new Error('Invalid WKT parentheses')
  return result
}

export function parseWktAois(text, fallbackName = 'Imported AOI') {
  const cleaned = text.trim().replace(/^SRID=\d+;/i, '')
  const typeMatch = cleaned.match(/^(POLYGON|MULTIPOLYGON)\s*/i)
  if (!typeMatch) throw new Error('Only POLYGON and MULTIPOLYGON WKT is supported')
  const tokens = tokenizeWkt(cleaned.slice(typeMatch[0].length))
  const coordinates = parseWktGroup(tokens, { index: 0 })
  const geometry = typeMatch[1].toUpperCase() === 'POLYGON'
    ? { type: 'Polygon', coordinates }
    : { type: 'MultiPolygon', coordinates }
  return polygonEntries(geometry, fallbackName)
}

export async function parseAoiFile(file) {
  const text = await file.text()
  const fallbackName = file.name.replace(/\.[^.]+$/, '').trim() || 'Imported AOI'
  const extension = file.name.split('.').pop()?.toLowerCase()
  if (extension === 'kml' || /<kml\b/i.test(text)) return parseKmlAois(text, fallbackName)
  if (['wkt', 'txt'].includes(extension) || /^\s*(?:SRID=\d+;)?(?:POLYGON|MULTIPOLYGON)\b/i.test(text)) {
    return parseWktAois(text, fallbackName)
  }
  return parseGeoJsonAois(text, fallbackName)
}
