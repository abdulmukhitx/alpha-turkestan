import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet-boundary-canvas'
import { tileUrl } from '../api'

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

export default function SplitMapView({
  periods, bounds, center, zoom,
  leftPeriod, leftIndex, onLeftPeriodChange, onLeftIndexChange,
  rightPeriod, rightIndex, onRightPeriodChange, onRightIndexChange,
  drawMode, onPolygonDrawn, clearSignal, finishSignal, onDrawPointsChange,
  lineDrawMode, onLineDrawn, lineClearSignal, lineFinishSignal, onLineDrawPointsChange,
  onPointClick, onMouseMove, onExitSplitMode,
}) {
  const containerRef = useRef(null)
  const leftElRef  = useRef(null)
  const rightElRef = useRef(null)
  const leftMapRef  = useRef(null)
  const rightMapRef = useRef(null)
  const leftDataRef  = useRef(null)
  const rightDataRef = useRef(null)
  const syncingRef = useRef(false)
  const [clipGeo, setClipGeo] = useState(undefined)

  const [sliderPct, setSliderPct] = useState(50)   // 0-100, % of container width
  const draggingRef = useRef(false)

  const drawModeRef = useRef(drawMode); drawModeRef.current = drawMode
  const onPolygonDrawnRef = useRef(onPolygonDrawn); onPolygonDrawnRef.current = onPolygonDrawn
  const onDrawPointsChangeRef = useRef(onDrawPointsChange); onDrawPointsChangeRef.current = onDrawPointsChange
  const drawPointsRef = useRef([])
  const drawLayerRef = useRef(null)
  const drawMarkersRef = useRef([])
  const resultLayerRef = useRef(null)
  const previewLineRef = useRef(null)

  const lineDrawModeRef = useRef(lineDrawMode); lineDrawModeRef.current = lineDrawMode
  const onLineDrawnRef = useRef(onLineDrawn); onLineDrawnRef.current = onLineDrawn
  const onLineDrawPointsChangeRef = useRef(onLineDrawPointsChange); onLineDrawPointsChangeRef.current = onLineDrawPointsChange
  const linePointsRef = useRef([])
  const lineLayerRef = useRef(null)
  const lineMarkersRef = useRef([])
  const lineResultRef = useRef(null)
  const linePreviewRef = useRef(null)

  const callbacksRef = useRef({ onPointClick, onMouseMove })
  callbacksRef.current = { onPointClick, onMouseMove }

  // ── init both instances once, synced ──────────────────────────
  useEffect(() => {
    const left  = L.map(leftElRef.current,  { center, zoom, zoomControl: false, attributionControl: false, preferCanvas: true })
    const right = L.map(rightElRef.current, { center, zoom, zoomControl: false, attributionControl: false, preferCanvas: true })
    leftMapRef.current = left
    rightMapRef.current = right

    L.tileLayer(BASEMAP_URL, { maxZoom: 18, subdomains: 'abcd', crossOrigin: true }).addTo(left)
    L.tileLayer(BASEMAP_URL, { maxZoom: 18, subdomains: 'abcd', crossOrigin: true }).addTo(right)

    function syncFrom(src, dst) {
      if (syncingRef.current) return
      syncingRef.current = true
      dst.setView(src.getCenter(), src.getZoom(), { animate: false })
      syncingRef.current = false
    }
    left.on('move zoom', () => syncFrom(left, right))
    right.on('move zoom', () => syncFrom(right, left))

    left.on('click', (e) => {
      const { lat, lng } = e.latlng
      if (lineDrawModeRef.current) { addLinePoint(left, lat, lng); return }
      if (drawModeRef.current) { addDrawPoint(left, lat, lng); return }
      callbacksRef.current.onPointClick?.(lat, lng)
    })
    left.on('dblclick', (e) => {
      if (lineDrawModeRef.current) { if (e.originalEvent) L.DomEvent.stop(e.originalEvent); finishLineDraw(left); return }
      if (!drawModeRef.current) return
      if (e.originalEvent) L.DomEvent.stop(e.originalEvent)
      finishDraw(left)
    })
    left.on('mousemove', (e) => {
      callbacksRef.current.onMouseMove?.(e.latlng.lat, e.latlng.lng)
      if (lineDrawModeRef.current && linePointsRef.current.length > 0) updateLinePreview(left, e.latlng)
      if (drawModeRef.current && drawPointsRef.current.length > 0) updatePreviewLine(left, e.latlng)
    })

    return () => { left.remove(); right.remove() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // boundary clip (best-effort — falls back to unclipped if it fails to load)
  useEffect(() => {
    let cancelled = false
    fetch('/turkestan_boundary.geojson')
      .then((r) => { if (!r.ok) throw new Error(); return r.json() })
      .then((geo) => { if (!cancelled) setClipGeo(geo) })
      .catch(() => { if (!cancelled) setClipGeo(null) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    const map = leftMapRef.current
    if (!map || !bounds) return
    map.fitBounds([[bounds[0], bounds[1]], [bounds[2], bounds[3]]], { padding: [20, 20] })
  }, [bounds])

  // ── data layers, one per side ──────────────────────────────────
  useEffect(() => {
    const map = leftMapRef.current
    if (!map || clipGeo === undefined) return
    if (leftDataRef.current) map.removeLayer(leftDataRef.current)
    const common = { tileSize: 256, minZoom: 4, maxZoom: 18, crossOrigin: true }
    const url = tileUrl(leftIndex, leftPeriod)
    leftDataRef.current = (clipGeo && L.TileLayer.boundaryCanvas)
      ? L.TileLayer.boundaryCanvas(url, { ...common, boundary: clipGeo }).addTo(map)
      : L.tileLayer(url, common).addTo(map)
  }, [leftIndex, leftPeriod, clipGeo])

  useEffect(() => {
    const map = rightMapRef.current
    if (!map || clipGeo === undefined) return
    if (rightDataRef.current) map.removeLayer(rightDataRef.current)
    const common = { tileSize: 256, minZoom: 4, maxZoom: 18, crossOrigin: true }
    const url = tileUrl(rightIndex, rightPeriod)
    rightDataRef.current = (clipGeo && L.TileLayer.boundaryCanvas)
      ? L.TileLayer.boundaryCanvas(url, { ...common, boundary: clipGeo }).addTo(map)
      : L.tileLayer(url, common).addTo(map)
  }, [rightIndex, rightPeriod, clipGeo])

  // cursor + dblclick-zoom toggling for the interactive (left) map
  useEffect(() => {
    const map = leftMapRef.current
    if (!map) return
    const container = map.getContainer()
    if (drawMode) { container.style.cursor = 'crosshair'; map.doubleClickZoom.disable() }
    else { container.style.cursor = ''; map.doubleClickZoom.enable(); clearDraw(map) }
  }, [drawMode])
  useEffect(() => {
    const map = leftMapRef.current
    if (!map) return
    const container = map.getContainer()
    if (lineDrawMode) { container.style.cursor = 'crosshair'; map.doubleClickZoom.disable() }
    else { container.style.cursor = ''; map.doubleClickZoom.enable(); clearLineDraw(map) }
  }, [lineDrawMode])

  useEffect(() => {
    const map = leftMapRef.current
    if (!map || clearSignal == null) return
    clearDraw(map)
    if (resultLayerRef.current) { map.removeLayer(resultLayerRef.current); resultLayerRef.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clearSignal])
  useEffect(() => {
    const map = leftMapRef.current
    if (!map || finishSignal == null) return
    finishDraw(map)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finishSignal])
  useEffect(() => {
    const map = leftMapRef.current
    if (!map || lineClearSignal == null) return
    clearLineDraw(map)
    if (lineResultRef.current) { map.removeLayer(lineResultRef.current); lineResultRef.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lineClearSignal])
  useEffect(() => {
    const map = leftMapRef.current
    if (!map || lineFinishSignal == null) return
    finishLineDraw(map)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lineFinishSignal])

  function addDrawPoint(map, lat, lng) {
    const pts = drawPointsRef.current
    if (pts.length >= 3) {
      const firstPx = map.latLngToContainerPoint(pts[0])
      const clickPx = map.latLngToContainerPoint([lat, lng])
      if (firstPx.distanceTo(clickPx) < 14) { finishDraw(map); return }
    }
    pts.push([lat, lng])
    const isFirst = pts.length === 1
    const m = L.circleMarker([lat, lng], { radius: isFirst ? 6 : 4, color: '#3B82F6', weight: 2, fillColor: isFirst ? '#FFFFFF' : '#3B82F6', fillOpacity: 1 }).addTo(map)
    drawMarkersRef.current.push(m)
    if (drawLayerRef.current) map.removeLayer(drawLayerRef.current)
    if (pts.length > 1) drawLayerRef.current = L.polyline(pts, { color: '#3B82F6', weight: 2 }).addTo(map)
    onDrawPointsChangeRef.current?.(pts.length)
  }
  function updatePreviewLine(map, latlng) {
    const last = drawPointsRef.current[drawPointsRef.current.length - 1]
    if (!last) return
    const path = [last, [latlng.lat, latlng.lng]]
    if (previewLineRef.current) previewLineRef.current.setLatLngs(path)
    else previewLineRef.current = L.polyline(path, { color: '#3B82F6', weight: 2, dashArray: '6 6', opacity: 0.85 }).addTo(map)
  }
  function clearDraw(map) {
    drawPointsRef.current = []
    if (drawLayerRef.current) { map.removeLayer(drawLayerRef.current); drawLayerRef.current = null }
    if (previewLineRef.current) { map.removeLayer(previewLineRef.current); previewLineRef.current = null }
    drawMarkersRef.current.forEach((m) => map.removeLayer(m))
    drawMarkersRef.current = []
    onDrawPointsChangeRef.current?.(0)
  }
  function finishDraw(map) {
    const pts = [...drawPointsRef.current]
    if (pts.length >= 2) {
      const [lat1, lng1] = pts[pts.length - 1]
      const [lat2, lng2] = pts[pts.length - 2]
      if (Math.abs(lat1 - lat2) < 1e-9 && Math.abs(lng1 - lng2) < 1e-9) pts.pop()
    }
    clearDraw(map)
    if (pts.length < 3) return
    if (resultLayerRef.current) { map.removeLayer(resultLayerRef.current); resultLayerRef.current = null }
    resultLayerRef.current = L.polygon(pts, { color: '#3B82F6', weight: 2, fillColor: '#3B82F6', fillOpacity: 0.2 }).addTo(map)
    const ring = pts.map(([lat, lng]) => [lng, lat])
    ring.push(ring[0])
    onPolygonDrawnRef.current?.({ type: 'Polygon', coordinates: [ring] })
  }

  function addLinePoint(map, lat, lng) {
    const pts = linePointsRef.current
    pts.push([lat, lng])
    const isFirst = pts.length === 1
    const m = L.circleMarker([lat, lng], { radius: isFirst ? 6 : 4, color: '#3B82F6', weight: 2, fillColor: isFirst ? '#FFFFFF' : '#3B82F6', fillOpacity: 1 }).addTo(map)
    lineMarkersRef.current.push(m)
    if (lineLayerRef.current) map.removeLayer(lineLayerRef.current)
    if (pts.length > 1) lineLayerRef.current = L.polyline(pts, { color: '#3B82F6', weight: 3, dashArray: '8 6' }).addTo(map)
    onLineDrawPointsChangeRef.current?.(pts.length)
  }
  function updateLinePreview(map, latlng) {
    const last = linePointsRef.current[linePointsRef.current.length - 1]
    if (!last) return
    const path = [last, [latlng.lat, latlng.lng]]
    if (linePreviewRef.current) linePreviewRef.current.setLatLngs(path)
    else linePreviewRef.current = L.polyline(path, { color: '#3B82F6', weight: 2, dashArray: '4 4', opacity: 0.7 }).addTo(map)
  }
  function clearLineDraw(map) {
    linePointsRef.current = []
    if (lineLayerRef.current) { map.removeLayer(lineLayerRef.current); lineLayerRef.current = null }
    if (linePreviewRef.current) { map.removeLayer(linePreviewRef.current); linePreviewRef.current = null }
    lineMarkersRef.current.forEach((m) => map.removeLayer(m))
    lineMarkersRef.current = []
    onLineDrawPointsChangeRef.current?.(0)
  }
  function finishLineDraw(map) {
    const pts = [...linePointsRef.current]
    if (pts.length >= 2) {
      const [lat1, lng1] = pts[pts.length - 1]
      const [lat2, lng2] = pts[pts.length - 2]
      if (Math.abs(lat1 - lat2) < 1e-9 && Math.abs(lng1 - lng2) < 1e-9) pts.pop()
    }
    clearLineDraw(map)
    if (pts.length < 2) return
    if (lineResultRef.current) { map.removeLayer(lineResultRef.current); lineResultRef.current = null }
    lineResultRef.current = L.polyline(pts, { color: '#3B82F6', weight: 3, dashArray: '8 6' }).addTo(map)
    const coordinates = pts.map(([lat, lng]) => [lng, lat])
    onLineDrawnRef.current?.({ type: 'LineString', coordinates })
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
  useEffect(() => {
    function onMove(e) {
      if (!draggingRef.current) return
      moveSliderTo(e.clientX)
    }
    function onUp() {
      if (!draggingRef.current) return
      draggingRef.current = false
      leftMapRef.current?.invalidateSize()
      rightMapRef.current?.invalidateSize()
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [])

  const leftLabel  = periods.find((p) => p.period_id === leftPeriod)?.label  || leftPeriod
  const rightLabel = periods.find((p) => p.period_id === rightPeriod)?.label || rightPeriod

  return (
    <section className="map-section split-view" ref={containerRef}>
      <div className="split-pane split-pane-left" ref={leftElRef} />
      <div className="split-pane split-pane-right" style={{ clipPath: `inset(0 0 0 ${sliderPct}%)` }} ref={rightElRef} />

      <div
        className="split-divider"
        style={{ left: `${sliderPct}%` }}
        onPointerDown={handlePointerDown}
      >
        <div className="split-handle">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M5 2 2 7l3 5M9 2l3 5-3 5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      </div>

      <div className="split-topbar">
        <select value={leftPeriod || ''} onChange={(e) => onLeftPeriodChange(e.target.value)}>
          {periods.map((p) => <option key={p.period_id} value={p.period_id}>{p.label}</option>)}
        </select>
        <select value={leftIndex} onChange={(e) => onLeftIndexChange(e.target.value)}>
          {INDEX_OPTIONS.map((opt) => <option key={opt.key} value={opt.key}>{opt.code}</option>)}
        </select>
        <span className="split-topbar-arrow">◄──►</span>
        <select value={rightPeriod || ''} onChange={(e) => onRightPeriodChange(e.target.value)}>
          {periods.map((p) => <option key={p.period_id} value={p.period_id}>{p.label}</option>)}
        </select>
        <select value={rightIndex} onChange={(e) => onRightIndexChange(e.target.value)}>
          {INDEX_OPTIONS.map((opt) => <option key={opt.key} value={opt.key}>{opt.code}</option>)}
        </select>
      </div>

      <div className="split-label split-label-left">{leftLabel} · {leftIndex.toUpperCase()}</div>
      <div className="split-label split-label-right">{rightLabel} · {rightIndex.toUpperCase()}</div>

      <div className="map-toolbar">
        <button className="map-tool-btn" title="Приближение" onClick={() => leftMapRef.current?.zoomIn()}>+</button>
        <button className="map-tool-btn" title="Отдаление" onClick={() => leftMapRef.current?.zoomOut()}>−</button>
        <div className="map-tool-sep" />
        <button
          className="map-tool-btn active"
          title="Выйти из режима сравнения"
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
