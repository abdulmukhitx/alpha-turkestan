import { useEffect, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { tileUrl } from '../api'
import { useI18n } from '../i18n.jsx'

// Same 7 physical indices the single-map view offers — "satellite" isn't a
// period-tied tile layer (it's the basemap), so it doesn't apply here.
const INDEX_OPTIONS = [
  { key: 'ndvi', code: 'NDVI' },
  { key: 'ndwi', code: 'NDWI' },
  { key: 'ndre', code: 'NDRE' },
  { key: 'ndmi', code: 'NDMI' },
  { key: 'bsi',  code: 'BSI' },
  { key: 'savi', code: 'SAVI' },
  { key: 'nbr',  code: 'NBR' },
]

const BASEMAP_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'

const DRAW_POINT_PAINT = {
  'circle-radius': ['case', ['==', ['get', 'first'], 1], 6, 4],
  'circle-color': ['case', ['==', ['get', 'first'], 1], '#FFFFFF', '#3B82F6'],
  'circle-stroke-color': '#3B82F6',
  'circle-stroke-width': 2,
}

function emptyFC() { return { type: 'FeatureCollection', features: [] } }

function baseStyle() {
  return {
    version: 8,
    sources: {
      basemap: { type: 'raster', tiles: [BASEMAP_URL], tileSize: 256 },
    },
    layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }],
  }
}

function makeMap(container, center, zoom) {
  return new maplibregl.Map({
    container,
    style: baseStyle(),
    center,
    zoom,
    attributionControl: true,
    dragRotate: false,
    pitchWithRotate: false,
  })
}

// Adds/refreshes the data (index/period) raster source+layer for one map.
function setDataLayer(map, index, period) {
  if (!map.isStyleLoaded()) return
  const url = tileUrl(index, period)
  const sourceId = 'data'
  if (map.getSource(sourceId)) {
    map.getSource(sourceId).setTiles([url])
  } else {
    map.addSource(sourceId, { type: 'raster', tiles: [url], tileSize: 256 })
    map.addLayer({ id: 'data-layer', type: 'raster', source: sourceId, paint: { 'raster-opacity': 0.9 } })
  }
}

// The data COG's own bbox is a rectangle larger than the oblast — same reason
// MapView.jsx's Leaflet layer needs leaflet-boundary-canvas on top of the
// per-pixel nodata mask. MapLibre's raster layers have no native "clip to
// polygon" primitive, so the standard equivalent is an inverted mask: one
// giant world-covering polygon with the oblast boundary punched out as a
// hole, painted (nearly) opaque, stacked directly above the data layer —
// MapLibre can't keep the raw basemap visible there without a custom WebGL
// layer, so this dark "spotlight" look is the closest native match.
const WORLD_RING = [[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]]

function extractExteriorRings(geo) {
  const rings = []
  function walk(obj) {
    if (!obj) return
    if (obj.type === 'FeatureCollection') obj.features.forEach(walk)
    else if (obj.type === 'GeometryCollection') obj.geometries.forEach(walk)
    else if (obj.type === 'Feature') walk(obj.geometry)
    else if (obj.type === 'Polygon') rings.push(obj.coordinates[0])
    else if (obj.type === 'MultiPolygon') obj.coordinates.forEach((poly) => rings.push(poly[0]))
  }
  walk(geo)
  return rings
}

function pointInRing(lng, lat, ring) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    const intersects = ((yi > lat) !== (yj > lat)) &&
      (lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi)
    if (intersects) inside = !inside
  }
  return inside
}

function insideBoundary(lng, lat, geo) {
  if (!geo) return true
  const rings = extractExteriorRings(geo)
  return rings.length === 0 || rings.some((ring) => pointInRing(lng, lat, ring))
}

function invertedMaskGeoJSON(geo) {
  return {
    type: 'Feature', properties: {},
    geometry: { type: 'Polygon', coordinates: [WORLD_RING, ...extractExteriorRings(geo)] },
  }
}

// Adds the boundary mask + white outline once clipGeo is available. Idempotent
// source checks make repeated React effects safe while the load listener is
// explicitly cleaned up when its dependencies change.
function applyBoundaryMask(map, clipGeo) {
  if (!clipGeo || map.getSource('mask')) return
  map.addSource('mask', { type: 'geojson', data: invertedMaskGeoJSON(clipGeo) })
  map.addLayer({ id: 'mask-layer', type: 'fill', source: 'mask', paint: { 'fill-color': '#0D1219', 'fill-opacity': 0.92 } })

  map.addSource('boundary', { type: 'geojson', data: clipGeo })
  map.addLayer({
    id: 'boundary-line', type: 'line', source: 'boundary',
    paint: { 'line-color': '#ffffff', 'line-width': 2, 'line-opacity': 0.9 },
  })
}

function ensureDrawSources(map) {
  if (map.getSource('draw-points')) return
  map.addSource('draw-points', { type: 'geojson', data: emptyFC() })
  map.addSource('draw-line', { type: 'geojson', data: emptyFC() })
  map.addSource('result-zone', { type: 'geojson', data: emptyFC() })
  map.addSource('result-transect', { type: 'geojson', data: emptyFC() })
  map.addLayer({ id: 'result-zone-fill', type: 'fill', source: 'result-zone',
    paint: { 'fill-color': '#3B82F6', 'fill-opacity': 0.2 } })
  map.addLayer({ id: 'result-zone-line', type: 'line', source: 'result-zone',
    paint: { 'line-color': '#3B82F6', 'line-width': 2 } })
  map.addLayer({ id: 'result-transect-line', type: 'line', source: 'result-transect',
    paint: { 'line-color': '#3B82F6', 'line-width': 3, 'line-dasharray': [2, 2] } })
  map.addLayer({ id: 'draw-line-layer', type: 'line', source: 'draw-line',
    paint: { 'line-color': '#3B82F6', 'line-width': 2 } })
  map.addLayer({ id: 'draw-points-layer', type: 'circle', source: 'draw-points', paint: DRAW_POINT_PAINT })
}

export default function SplitMapView({
  periods, bounds, center, zoom,
  leftPeriod, leftIndex, onLeftPeriodChange, onLeftIndexChange,
  rightPeriod, rightIndex, onRightPeriodChange, onRightIndexChange,
  drawMode, onPolygonDrawn, clearSignal, finishSignal, onDrawPointsChange,
  lineDrawMode, onLineDrawn, lineClearSignal, lineFinishSignal, onLineDrawPointsChange,
  onPointClick, onMouseMove, onExitSplitMode,
}) {
  const { t, periodLabel } = useI18n()
  const containerRef = useRef(null)
  const leftElRef  = useRef(null)
  const rightElRef = useRef(null)
  const leftMapRef  = useRef(null)
  const rightMapRef = useRef(null)
  const syncingRef = useRef(false)
  const [clipGeo, setClipGeo] = useState(undefined)
  const clipGeoRef = useRef(clipGeo); clipGeoRef.current = clipGeo
  const viewContextRef = useRef({ leftPeriod, leftIndex, rightPeriod, rightIndex })
  viewContextRef.current = { leftPeriod, leftIndex, rightPeriod, rightIndex }

  const [sliderPct, setSliderPct] = useState(50)   // 0-100, % of container width
  const draggingRef = useRef(false)

  const drawModeRef = useRef(drawMode); drawModeRef.current = drawMode
  const onPolygonDrawnRef = useRef(onPolygonDrawn); onPolygonDrawnRef.current = onPolygonDrawn
  const onDrawPointsChangeRef = useRef(onDrawPointsChange); onDrawPointsChangeRef.current = onDrawPointsChange
  const drawPointsRef = useRef([])   // [{lng,lat}, ...] in progress

  const lineDrawModeRef = useRef(lineDrawMode); lineDrawModeRef.current = lineDrawMode
  const onLineDrawnRef = useRef(onLineDrawn); onLineDrawnRef.current = onLineDrawn
  const onLineDrawPointsChangeRef = useRef(onLineDrawPointsChange); onLineDrawPointsChangeRef.current = onLineDrawPointsChange
  const linePointsRef = useRef([])

  const callbacksRef = useRef({ onPointClick, onMouseMove })
  callbacksRef.current = { onPointClick, onMouseMove }

  // ── init both instances once, synced ──────────────────────────
  useEffect(() => {
    const c = center ? [center[1], center[0]] : [68.36, 43.39]   // App passes [lat,lon] — MapLibre wants [lon,lat]
    const left  = makeMap(leftElRef.current,  c, zoom || 7)
    const right = makeMap(rightElRef.current, c, zoom || 7)
    leftMapRef.current = left
    rightMapRef.current = right

    function syncFrom(src, dst) {
      if (syncingRef.current) return
      syncingRef.current = true
      dst.jumpTo({ center: src.getCenter(), zoom: src.getZoom(), bearing: src.getBearing(), pitch: src.getPitch() })
      syncingRef.current = false
    }
    left.on('move', () => syncFrom(left, right))
    right.on('move', () => syncFrom(right, left))

    left.on('click', (e) => {
      const { lng, lat } = e.lngLat
      if (!insideBoundary(lng, lat, clipGeoRef.current)) return
      if (lineDrawModeRef.current) { addLinePoint(left, lng, lat); return }
      if (drawModeRef.current) { addDrawPoint(left, lng, lat); return }
      callbacksRef.current.onPointClick?.(lat, lng, {
        period: viewContextRef.current.leftPeriod,
        layer: viewContextRef.current.leftIndex,
        pane: 'left',
      })
    })
    left.on('dblclick', (e) => {
      if (lineDrawModeRef.current) { e.preventDefault(); finishLineDraw(); return }
      if (!drawModeRef.current) return
      e.preventDefault()
      finishDraw()
    })
    left.on('mousemove', (e) => {
      callbacksRef.current.onMouseMove?.(e.lngLat.lat, e.lngLat.lng)
    })
    right.on('click', (e) => {
      const { lng, lat } = e.lngLat
      if (!insideBoundary(lng, lat, clipGeoRef.current)) return
      callbacksRef.current.onPointClick?.(lat, lng, {
        period: viewContextRef.current.rightPeriod,
        layer: viewContextRef.current.rightIndex,
        pane: 'right',
      })
    })
    right.on('mousemove', (e) => {
      callbacksRef.current.onMouseMove?.(e.lngLat.lat, e.lngLat.lng)
    })

    return () => { left.remove(); right.remove() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // boundary clip outline (best-effort — falls back to unclipped if it fails to load)
  useEffect(() => {
    let cancelled = false
    fetch('/turkestan_boundary.geojson')
      .then((r) => { if (!r.ok) throw new Error(); return r.json() })
      .then((geo) => { if (!cancelled) setClipGeo(geo) })
      .catch(() => { if (!cancelled) setClipGeo(null) })
    return () => { cancelled = true }
  }, [])

  // Fit BOTH instances directly to the region bbox — don't rely solely on the
  // move-sync relay for the initial framing, since a fitBounds fired only on
  // the left map depends on that 'move' event propagating through syncFrom
  // before the right map has necessarily finished loading its own style.
  useEffect(() => {
    if (!bounds) return
    const bbox = [[bounds[1], bounds[0]], [bounds[3], bounds[2]]]
    const doFit = (map) => map.fitBounds(bbox, { padding: 20, animate: false })
    for (const map of [leftMapRef.current, rightMapRef.current]) {
      if (!map) continue
      if (map.isStyleLoaded()) doFit(map)
      else map.once('load', () => doFit(map))
    }
  }, [bounds])

  // ── data layers, one per side — (re)attached whenever the style (re)loads ──
  useEffect(() => {
    const map = leftMapRef.current
    if (!map) return
    const apply = () => {
      setDataLayer(map, leftIndex, leftPeriod)
      applyBoundaryMask(map, clipGeo)
      ensureDrawSources(map)
    }
    if (map.isStyleLoaded()) {
      apply()
      return
    }
    map.on('load', apply)
    return () => map.off('load', apply)
  }, [leftIndex, leftPeriod, clipGeo])

  useEffect(() => {
    const map = rightMapRef.current
    if (!map) return
    const apply = () => {
      setDataLayer(map, rightIndex, rightPeriod)
      applyBoundaryMask(map, clipGeo)
      ensureDrawSources(map)
    }
    if (map.isStyleLoaded()) {
      apply()
      return
    }
    map.on('load', apply)
    return () => map.off('load', apply)
  }, [rightIndex, rightPeriod, clipGeo])

  // cursor + dblclick-zoom toggling for the interactive (left) map
  useEffect(() => {
    const map = leftMapRef.current
    if (!map) return
    const drawing = drawMode || lineDrawMode
    map.getCanvas().style.cursor = drawing ? 'crosshair' : ''
    if (drawing) map.doubleClickZoom.disable()
    else map.doubleClickZoom.enable()
    if (!drawMode) clearDraw()
    if (!lineDrawMode) clearLineDraw()
  }, [drawMode, lineDrawMode])

  useEffect(() => { if (clearSignal != null) clearDraw(true) /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [clearSignal])
  useEffect(() => { if (finishSignal != null) finishDraw() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [finishSignal])
  useEffect(() => { if (lineClearSignal != null) clearLineDraw(true) /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [lineClearSignal])
  useEffect(() => { if (lineFinishSignal != null) finishLineDraw() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [lineFinishSignal])

  function drawPointsFC(pts) {
    return {
      type: 'FeatureCollection',
      features: pts.map((p, i) => ({
        type: 'Feature', properties: { first: i === 0 ? 1 : 0 },
        geometry: { type: 'Point', coordinates: [p.lng, p.lat] },
      })),
    }
  }
  function drawLineFC(pts) {
    return {
      type: 'FeatureCollection',
      features: pts.length > 1
        ? [{ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: pts.map((p) => [p.lng, p.lat]) } }]
        : [],
    }
  }
  function setSourceDataOnBoth(sourceId, data) {
    for (const map of [leftMapRef.current, rightMapRef.current]) {
      map?.getSource(sourceId)?.setData(data)
    }
  }
  function repaintDraw() {
    setSourceDataOnBoth('draw-points', drawPointsFC(drawPointsRef.current))
    setSourceDataOnBoth('draw-line', drawLineFC(drawPointsRef.current))
  }
  function repaintLine() {
    setSourceDataOnBoth('draw-points', drawPointsFC(linePointsRef.current))
    setSourceDataOnBoth('draw-line', drawLineFC(linePointsRef.current))
  }

  function addDrawPoint(map, lng, lat) {
    const pts = drawPointsRef.current
    if (pts.length >= 3) {
      const firstPx = map.project([pts[0].lng, pts[0].lat])
      const clickPx = map.project([lng, lat])
      if (Math.hypot(firstPx.x - clickPx.x, firstPx.y - clickPx.y) < 14) { finishDraw(); return }
    }
    pts.push({ lng, lat })
    repaintDraw()
    onDrawPointsChangeRef.current?.(pts.length)
  }
  function clearDraw(clearResult = false) {
    drawPointsRef.current = []
    repaintDraw()
    if (clearResult) setSourceDataOnBoth('result-zone', emptyFC())
    onDrawPointsChangeRef.current?.(0)
  }
  function finishDraw() {
    const pts = [...drawPointsRef.current]
    clearDraw()
    if (pts.length < 3) return
    const ring = pts.map((p) => [p.lng, p.lat])
    ring.push(ring[0])
    const geometry = { type: 'Polygon', coordinates: [ring] }
    setSourceDataOnBoth('result-zone', { type: 'Feature', properties: {}, geometry })
    onPolygonDrawnRef.current?.(geometry, {
      period: viewContextRef.current.leftPeriod,
      layer: viewContextRef.current.leftIndex,
      pane: 'left',
    })
  }

  function addLinePoint(map, lng, lat) {
    const pts = linePointsRef.current
    pts.push({ lng, lat })
    repaintLine()
    onLineDrawPointsChangeRef.current?.(pts.length)
  }
  function clearLineDraw(clearResult = false) {
    linePointsRef.current = []
    repaintLine()
    if (clearResult) setSourceDataOnBoth('result-transect', emptyFC())
    onLineDrawPointsChangeRef.current?.(0)
  }
  function finishLineDraw() {
    const pts = [...linePointsRef.current]
    clearLineDraw()
    if (pts.length < 2) return
    const geometry = { type: 'LineString', coordinates: pts.map((p) => [p.lng, p.lat]) }
    setSourceDataOnBoth('result-transect', { type: 'Feature', properties: {}, geometry })
    onLineDrawnRef.current?.(geometry, {
      period: viewContextRef.current.leftPeriod,
      layer: viewContextRef.current.leftIndex,
      pane: 'left',
    })
  }

  // ── divider drag ────────────────────────────────────────────────
  // Window-level listeners (not element-scoped + setPointerCapture) so a fast
  // drag that leaves the thin 2px divider strip keeps tracking the pointer.
  function moveSliderTo(clientX) {
    const rect = containerRef.current.getBoundingClientRect()
    const pct = ((clientX - rect.left) / rect.width) * 100
    setSliderPct(Math.max(6, Math.min(94, pct)))
  }
  function handlePointerDown(e) {
    draggingRef.current = true
    moveSliderTo(e.clientX)
  }
  function handleDividerKeyDown(e) {
    const changes = { ArrowLeft: -2, ArrowRight: 2, Home: -94, End: 94 }
    if (!(e.key in changes)) return
    e.preventDefault()
    setSliderPct((value) => {
      if (e.key === 'Home') return 6
      if (e.key === 'End') return 94
      return Math.max(6, Math.min(94, value + changes[e.key]))
    })
  }
  useEffect(() => {
    function onMove(e) {
      if (!draggingRef.current) return
      moveSliderTo(e.clientX)
    }
    function onUp() {
      if (!draggingRef.current) return
      draggingRef.current = false
      leftMapRef.current?.resize()
      rightMapRef.current?.resize()
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [])

  // both instances must repaint their viewport whenever their box changes size
  useEffect(() => {
    leftMapRef.current?.resize()
    rightMapRef.current?.resize()
  }, [sliderPct])

  const leftLabel  = periodLabel(periods.find((p) => p.period_id === leftPeriod) || leftPeriod)
  const rightLabel = periodLabel(periods.find((p) => p.period_id === rightPeriod) || rightPeriod)

  return (
    <section className="map-section split-view" id="map-workspace" tabIndex={-1} aria-label={t('compare.aria')} ref={containerRef}>
      <div className="split-pane split-pane-left" ref={leftElRef} />
      <div className="split-pane split-pane-right" style={{ clipPath: `inset(0 0 0 ${sliderPct}%)` }} ref={rightElRef} />

      <div
        className="split-divider"
        style={{ left: `${sliderPct}%` }}
        onPointerDown={handlePointerDown}
        onKeyDown={handleDividerKeyDown}
        role="slider"
        tabIndex={0}
        aria-label={t('compare.divider')}
        aria-valuemin={6}
        aria-valuemax={94}
        aria-valuenow={Math.round(sliderPct)}
      >
        <div className="split-handle">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M5 2 2 7l3 5M9 2l3 5-3 5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      </div>

      <div className="split-topbar">
        <select aria-label={t('compare.leftPeriod')} value={leftPeriod || ''} onChange={(e) => onLeftPeriodChange(e.target.value)}>
          {periods.map((p) => <option key={p.period_id} value={p.period_id}>{periodLabel(p)}</option>)}
        </select>
        <select aria-label={t('compare.leftIndex')} value={leftIndex} onChange={(e) => onLeftIndexChange(e.target.value)}>
          {INDEX_OPTIONS.map((opt) => <option key={opt.key} value={opt.key}>{opt.code}</option>)}
        </select>
        <span className="split-topbar-arrow">◄──►</span>
        <select aria-label={t('compare.rightPeriod')} value={rightPeriod || ''} onChange={(e) => onRightPeriodChange(e.target.value)}>
          {periods.map((p) => <option key={p.period_id} value={p.period_id}>{periodLabel(p)}</option>)}
        </select>
        <select aria-label={t('compare.rightIndex')} value={rightIndex} onChange={(e) => onRightIndexChange(e.target.value)}>
          {INDEX_OPTIONS.map((opt) => <option key={opt.key} value={opt.key}>{opt.code}</option>)}
        </select>
      </div>

      <div className="split-label split-label-left">{leftLabel} · {leftIndex.toUpperCase()}</div>
      <div className="split-label split-label-right">{rightLabel} · {rightIndex.toUpperCase()}</div>

      <div className="map-toolbar" role="toolbar" aria-label={t('compare.toolbar')}>
        <button type="button" className="map-tool-btn" title={t('compare.zoomIn')} aria-label={t('compare.zoomIn')} onClick={() => leftMapRef.current?.zoomIn()}>+</button>
        <button type="button" className="map-tool-btn" title={t('compare.zoomOut')} aria-label={t('compare.zoomOut')} onClick={() => leftMapRef.current?.zoomOut()}>−</button>
        <div className="map-tool-sep" />
        <button
          type="button"
          className="map-tool-btn active"
          title={t('compare.exit')}
          aria-label={t('compare.exit')}
          aria-pressed="true"
          onClick={onExitSplitMode}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <rect x="1.5" y="2.5" width="5.5" height="11" rx="1" stroke="currentColor" strokeWidth="1.3" />
            <rect x="9" y="2.5" width="5.5" height="11" rx="1" stroke="currentColor" strokeWidth="1.3" />
          </svg>
        </button>
      </div>
    </section>
  )
}
