import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import 'leaflet-boundary-canvas'   // attaches L.TileLayer.boundaryCanvas
import { tileUrl } from '../api'

const BASEMAPS = {
  satellite: {
    name: 'Спутник',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: '© Esri',
  },
  terrain: {
    name: 'Рельеф',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}',
    attribution: '© Esri',
  },
  dark: {
    name: 'Тёмная',
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
    attribution: '© CARTO',
  },
}
const BASEMAP_ORDER = ['satellite', 'terrain', 'dark']

export default function MapView({ activeLayer, opacity, bounds, center, zoom, onPointClick, onMouseMove, onZoomChange }) {
  const elRef       = useRef(null)
  const mapRef      = useRef(null)
  const basemapRef  = useRef(null)
  const basemapKind = useRef('satellite')
  const overlaysRef = useRef({})
  const markerRef   = useRef(null)
  const boundaryRef = useRef(null)
  const ringsRef    = useRef(null)   // oblast rings [[lng,lat],...] for point-in-polygon
  const boundsRef   = useRef(bounds)
  const callbacksRef = useRef({ onPointClick, onMouseMove, onZoomChange })
  callbacksRef.current = { onPointClick, onMouseMove, onZoomChange }

  const [zoomLevel, setZoomLevel] = useState(zoom)
  const [hover, setHover] = useState(null)
  const [basemapName, setBasemapName] = useState(BASEMAPS.satellite.name)
  // undefined = boundary still loading, object = clip GeoJSON, null = no clip (fallback)
  const [clipGeo, setClipGeo] = useState(undefined)

  // init map once
  useEffect(() => {
    const map = L.map(elRef.current, {
      center, zoom, zoomControl: false, attributionControl: false, preferCanvas: true,
    })
    mapRef.current = map

    // basemap stays UNclipped — visible across the whole view
    basemapRef.current = L.tileLayer(BASEMAPS.satellite.url, {
      maxZoom: 18,
      subdomains: 'abcd',
      attribution: BASEMAPS.satellite.attribution,
    }).addTo(map)

    map.on('click', (e) => {
      const { lat, lng } = e.latlng
      if (!insideAOI(lat, lng, ringsRef.current, boundsRef.current)) return
      placeMarker(map, markerRef, lat, lng)
      callbacksRef.current.onPointClick(lat, lng)
    })
    map.on('mousemove', (e) => {
      setHover(e.latlng)
      callbacksRef.current.onMouseMove(e.latlng.lat, e.latlng.lng)
    })
    map.on('zoomend', () => {
      setZoomLevel(map.getZoom())
      callbacksRef.current.onZoomChange(map.getZoom())
    })

    return () => map.remove()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // keep bbox ref current (used for fallback click-gating and fit button)
  useEffect(() => { boundsRef.current = bounds }, [bounds])

  // load the real oblast boundary once → outline + remember it for clipping data tiles
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    let cancelled = false

    fetch('/turkestan_boundary.geojson')
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })
      .then((geo) => {
        if (cancelled) return

        // outer rings (lng,lat) for point-in-polygon click gating
        const rings = []
        for (const f of geo.features || [geo]) {
          const g = f.geometry || f
          const polys = g.type === 'MultiPolygon' ? g.coordinates
                      : g.type === 'Polygon'      ? [g.coordinates] : []
          for (const poly of polys) rings.push(poly[0])
        }
        ringsRef.current = rings.length ? rings : null

        // dashed boundary outline (no fill — basemap shows through)
        boundaryRef.current = L.geoJSON(geo, {
          style: { color: '#2563EB', weight: 2, fill: false, opacity: 0.9, dashArray: '5 4' },
          interactive: false,
        }).addTo(map)
        if (boundaryRef.current.getBounds) {
          map.fitBounds(boundaryRef.current.getBounds(), { padding: [16, 16] })
        }

        setClipGeo(geo)   // data layers will be clipped to this
      })
      .catch(() => {
        if (cancelled) return
        const b = boundsRef.current
        if (b) {
          const lb = [[b[0], b[1]], [b[2], b[3]]]
          boundaryRef.current = L.rectangle(lb, {
            color: '#2563EB', weight: 1.5, fill: false, opacity: 0.6, dashArray: '6 4',
          }).addTo(map)
          map.fitBounds(lb, { padding: [20, 20] })
        }
        setClipGeo(null)  // no clip available — data shown unclipped
      })

    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // fit when bbox bounds arrive but boundary hasn't loaded yet (first paint)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !bounds || boundaryRef.current) return
    map.fitBounds([[bounds[0], bounds[1]], [bounds[2], bounds[3]]], { padding: [20, 20] })
  }, [bounds])

  // create/switch the active data layer — clipped to the oblast boundary so the
  // rectangular AOI never spills past the region (basemap stays visible underneath)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !activeLayer) return
    if (clipGeo === undefined) return   // wait until boundary load resolves

    if (!overlaysRef.current[activeLayer]) {
      const common = { opacity, tileSize: 256, minZoom: 4, maxZoom: 18, crossOrigin: true }
      const url = tileUrl(activeLayer)
      const layer = (clipGeo && L.TileLayer.boundaryCanvas)
        ? L.TileLayer.boundaryCanvas(url, { ...common, boundary: clipGeo })
        : L.tileLayer(url, common)
      layer.addTo(map)
      overlaysRef.current[activeLayer] = layer
      boundaryRef.current?.bringToFront?.()   // keep outline above the data
    }
    Object.entries(overlaysRef.current).forEach(([id, layer]) => {
      layer.setOpacity(id === activeLayer ? opacity : 0)
    })
  }, [activeLayer, opacity, clipGeo])

  function zoomIn()  { mapRef.current?.zoomIn() }
  function zoomOut() { mapRef.current?.zoomOut() }
  function fit() {
    const map = mapRef.current
    if (!map) return
    if (boundaryRef.current?.getBounds) { map.fitBounds(boundaryRef.current.getBounds(), { padding: [16, 16] }); return }
    const b = boundsRef.current
    if (b) map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: [20, 20] })
  }
  function toggleBasemap() {
    const idx = BASEMAP_ORDER.indexOf(basemapKind.current)
    const next = BASEMAP_ORDER[(idx + 1) % BASEMAP_ORDER.length]
    basemapKind.current = next
    basemapRef.current.setUrl(BASEMAPS[next].url)
    setBasemapName(BASEMAPS[next].name)
  }

  return (
    <section className="map-section">
      <div ref={elRef} id="map" />

      <div className="map-toolbar">
        <button className="map-tool-btn" title="Приближение" onClick={zoomIn}>+</button>
        <button className="map-tool-btn" title="Отдаление" onClick={zoomOut}>−</button>
        <div className="map-tool-sep" />
        <button className="map-tool-btn" title="По всей области" onClick={fit}>⊕</button>
        <button className="map-tool-btn" title={`Базовая карта: ${basemapName} (нажмите для переключения)`} onClick={toggleBasemap}>⊞</button>
      </div>

      <div className="map-footer">
        <span>Zoom: {zoomLevel}</span>
        <span className="map-footer-sep">·</span>
        <span>EPSG:32641</span>
        <span className="map-footer-sep">·</span>
        <span>{hover ? `${hover.lat.toFixed(5)}°N · ${hover.lng.toFixed(5)}°E` : 'Наведите курсор на карту'}</span>
      </div>
    </section>
  )
}

function placeMarker(map, markerRef, lat, lng) {
  if (markerRef.current) map.removeLayer(markerRef.current)
  const icon = L.divIcon({ className: '', html: '<div class="pulse-marker"></div>', iconSize: [14, 14], iconAnchor: [7, 7] })
  markerRef.current = L.marker([lat, lng], { icon, zIndexOffset: 1000 }).addTo(map)
}

// Allow a click if it falls inside the oblast polygon (preferred) or, before the
// boundary has loaded, inside the COG bbox.
function insideAOI(lat, lng, rings, bbox) {
  if (rings && rings.length) return rings.some((r) => pointInRing(lng, lat, r))
  if (bbox) return lat >= bbox[0] && lat <= bbox[2] && lng >= bbox[1] && lng <= bbox[3]
  return true
}

// ray-casting point-in-polygon; ring is [[lng,lat], ...]
function pointInRing(lng, lat, ring) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1]
    const xj = ring[j][0], yj = ring[j][1]
    const hit = ((yi > lat) !== (yj > lat)) && (lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi)
    if (hit) inside = !inside
  }
  return inside
}
