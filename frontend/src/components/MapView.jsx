import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import 'leaflet-boundary-canvas'   // attaches L.TileLayer.boundaryCanvas
import { tileUrl, changeTileUrl, forecastTileUrl } from '../api'
import { useI18n } from '../i18n.jsx'

// Change-detection overlay sits on top of the normal index layer (not instead
// of it) at this fixed opacity — see ChangeDetectionBar for period/index pick.
const CHANGE_OVERLAY_OPACITY = 0.75

const BASEMAPS = {
  satellite: {
    name: 'map.basemapSatellite',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: '© Esri',
  },
  terrain: {
    name: 'map.basemapTerrain',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}',
    attribution: '© Esri',
  },
  dark: {
    name: 'map.basemapDark',
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
    attribution: '© CARTO',
  },
}
const BASEMAP_ORDER = ['satellite', 'terrain', 'dark']

const LABELS_URL = 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager_only_labels/{z}/{x}/{y}.png'

export default function MapView({
  activeLayer, period, periods = [], opacity, bounds, center, zoom, initialBasemap = 'satellite', onPointClick, onMouseMove, onZoomChange,
  drawMode, onPolygonDrawn, clearSignal, finishSignal, onDrawPointsChange,
  zoneGeometry, zoneEditMode, onPolygonEdited, zoneFocusSignal,
  lineDrawMode, onLineDrawn, lineClearSignal, lineFinishSignal, onLineDrawPointsChange,
  changeMode, changeIndex, changePeriodBefore, changePeriodAfter,
  forecastMode, forecastYear,
}) {
  const { t } = useI18n()
  const preferredBasemap = BASEMAPS[initialBasemap] ? initialBasemap : 'satellite'
  const elRef       = useRef(null)
  const mapRef      = useRef(null)
  const basemapRef  = useRef(null)
  const basemapKind = useRef(preferredBasemap)
  const labelsRef   = useRef(null)
  const overlaysRef = useRef({})
  const changeOverlayRef    = useRef(null)
  const changeOverlayKeyRef = useRef(null)
  const markerRef   = useRef(null)
  const boundaryRef = useRef(null)
  const ringsRef    = useRef(null)   // oblast rings [[lng,lat],...] for point-in-polygon
  const boundsRef   = useRef(bounds)
  const callbacksRef = useRef({ onPointClick, onMouseMove, onZoomChange })
  callbacksRef.current = { onPointClick, onMouseMove, onZoomChange }

  const drawModeRef    = useRef(drawMode)
  drawModeRef.current  = drawMode
  const onPolygonDrawnRef = useRef(onPolygonDrawn)
  onPolygonDrawnRef.current = onPolygonDrawn
  const onPolygonEditedRef = useRef(onPolygonEdited)
  onPolygonEditedRef.current = onPolygonEdited
  const onDrawPointsChangeRef = useRef(onDrawPointsChange)
  onDrawPointsChangeRef.current = onDrawPointsChange
  const drawPointsRef   = useRef([])   // [[lat,lng],...] in progress
  const drawLayerRef    = useRef(null) // live polyline/polygon while drawing
  const drawMarkersRef  = useRef([])
  const resultLayerRef  = useRef(null) // final drawn polygon overlay
  const editMarkersRef  = useRef([])   // draggable handles for the active saved/drawn zone
  const previewLineRef  = useRef(null) // rubber-band line: last point → cursor

  const lineDrawModeRef    = useRef(lineDrawMode)
  lineDrawModeRef.current  = lineDrawMode
  const onLineDrawnRef = useRef(onLineDrawn)
  onLineDrawnRef.current = onLineDrawn
  const onLineDrawPointsChangeRef = useRef(onLineDrawPointsChange)
  onLineDrawPointsChangeRef.current = onLineDrawPointsChange
  const linePointsRef    = useRef([])   // [[lat,lng],...] in progress
  const lineLayerRef     = useRef(null) // live polyline while drawing
  const lineMarkersRef   = useRef([])
  const lineResultRef    = useRef(null) // final drawn line overlay
  const linePreviewRef   = useRef(null) // rubber-band line: last point → cursor

  const [zoomLevel, setZoomLevel] = useState(zoom)
  const [hover, setHover] = useState(null)
  const [basemapKey, setBasemapKey] = useState(preferredBasemap)
  const [labelsOn, setLabelsOn] = useState(true)
  // undefined = boundary still loading, object = clip GeoJSON, null = no clip (fallback)
  const [clipGeo, setClipGeo] = useState(undefined)

  // init map once
  useEffect(() => {
    const map = L.map(elRef.current, {
      center, zoom, zoomControl: false, attributionControl: true, preferCanvas: true,
    })
    mapRef.current = map

    // basemap stays UNclipped — visible across the whole view
    // crossOrigin so html2canvas (zone-report screenshot) can read these tiles
    // without tainting its canvas — same reason the index tile layers set it below.
    basemapRef.current = L.tileLayer(BASEMAPS[preferredBasemap].url, {
      maxZoom: 18,
      subdomains: 'abcd',
      attribution: BASEMAPS[preferredBasemap].attribution,
      crossOrigin: true,
    }).addTo(map)

    // dedicated pane so labels always render above index layers, regardless of add order
    map.createPane('labels')
    map.getPane('labels').style.zIndex = 650
    map.getPane('labels').style.pointerEvents = 'none'
    labelsRef.current = L.tileLayer(LABELS_URL, {
      maxZoom: 18,
      subdomains: 'abcd',
      attribution: '© CARTO',
      crossOrigin: true,
      pane: 'labels',
    }).addTo(map)

    map.on('click', (e) => {
      const { lat, lng } = e.latlng
      if (lineDrawModeRef.current) {
        addLinePoint(map, lat, lng)
        return
      }
      if (drawModeRef.current) {
        addDrawPoint(map, lat, lng)
        return
      }
      if (!insideAOI(lat, lng, ringsRef.current, boundsRef.current)) return
      placeMarker(map, markerRef, lat, lng)
      callbacksRef.current.onPointClick(lat, lng)
    })
    map.on('dblclick', (e) => {
      if (lineDrawModeRef.current) {
        if (e.originalEvent) L.DomEvent.stop(e.originalEvent)
        finishLineDraw(map)
        return
      }
      if (!drawModeRef.current) return
      if (e.originalEvent) L.DomEvent.stop(e.originalEvent)
      finishDraw(map)
    })
    map.on('mousemove', (e) => {
      setHover(e.latlng)
      callbacksRef.current.onMouseMove(e.latlng.lat, e.latlng.lng)
      if (lineDrawModeRef.current && linePointsRef.current.length > 0) {
        updateLinePreview(map, e.latlng)
      }
      if (drawModeRef.current && drawPointsRef.current.length > 0) {
        updatePreviewLine(map, e.latlng)
      }
    })
    map.on('zoomend', () => {
      setZoomLevel(map.getZoom())
      callbacksRef.current.onZoomChange(map.getZoom())
    })

    return () => map.remove()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!BASEMAPS[initialBasemap] || !basemapRef.current) return
    basemapKind.current = initialBasemap
    basemapRef.current.setUrl(BASEMAPS[initialBasemap].url)
    setBasemapKey(initialBasemap)
  }, [initialBasemap])

  // keep bbox ref current (used for fallback click-gating and fit button)
  useEffect(() => { boundsRef.current = bounds }, [bounds])

  // Drawing modes are mutually exclusive in App; one combined effect keeps
  // cursor/zoom state correct during the transition between them.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const container = map.getContainer()
    const drawing = drawMode || lineDrawMode
    container.style.cursor = drawing ? 'crosshair' : ''
    if (drawing) map.doubleClickZoom.disable()
    else map.doubleClickZoom.enable()
    if (!drawMode) clearDraw(map)
    if (!lineDrawMode) clearLineDraw(map)
  }, [drawMode, lineDrawMode])

  // external "clear" trigger (e.g. a Clear button in the side panel)
  useEffect(() => {
    const map = mapRef.current
    if (!map || clearSignal == null) return
    clearDraw(map)
    clearResultPolygon(map)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clearSignal])

  // external "finish" trigger (explicit button — double-click can be flaky on trackpads)
  useEffect(() => {
    const map = mapRef.current
    if (!map || finishSignal == null) return
    finishDraw(map)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finishSignal])

  // Keep the map overlay in sync when a saved zone is reopened or its
  // geometry changes. Vertex handles are ordinary draggable Leaflet markers,
  // so editing works without an additional drawing plugin.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    renderResultPolygon(map, zoneGeometry, zoneEditMode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoneGeometry, zoneEditMode])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !zoneFocusSignal || !resultLayerRef.current?.getBounds) return
    const zoneBounds = resultLayerRef.current.getBounds()
    if (zoneBounds.isValid()) map.fitBounds(zoneBounds, { padding: [32, 32], maxZoom: 14 })
  }, [zoneFocusSignal])

  useEffect(() => {
    const map = mapRef.current
    if (!map || lineClearSignal == null) return
    clearLineDraw(map)
    if (lineResultRef.current) { map.removeLayer(lineResultRef.current); lineResultRef.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lineClearSignal])

  useEffect(() => {
    const map = mapRef.current
    if (!map || lineFinishSignal == null) return
    finishLineDraw(map)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lineFinishSignal])

  function addDrawPoint(map, lat, lng) {
    const pts = drawPointsRef.current

    // clicking back near the first vertex closes the polygon, same as most GIS tools
    if (pts.length >= 3) {
      const firstPx = map.latLngToContainerPoint(pts[0])
      const clickPx = map.latLngToContainerPoint([lat, lng])
      if (firstPx.distanceTo(clickPx) < 14) {
        finishDraw(map)
        return
      }
    }

    pts.push([lat, lng])
    const isFirst = pts.length === 1
    const m = L.circleMarker([lat, lng], {
      radius: isFirst ? 6 : 4, color: '#3B82F6', weight: 2, fillColor: isFirst ? '#FFFFFF' : '#3B82F6', fillOpacity: 1,
    }).addTo(map)
    drawMarkersRef.current.push(m)

    if (drawLayerRef.current) map.removeLayer(drawLayerRef.current)
    if (pts.length > 1) {
      drawLayerRef.current = L.polyline(pts, { color: '#3B82F6', weight: 2 }).addTo(map)
    }
    onDrawPointsChangeRef.current?.(pts.length)
  }

  function updatePreviewLine(map, latlng) {
    const last = drawPointsRef.current[drawPointsRef.current.length - 1]
    if (!last) return
    const path = [last, [latlng.lat, latlng.lng]]
    if (previewLineRef.current) {
      previewLineRef.current.setLatLngs(path)
    } else {
      previewLineRef.current = L.polyline(path, {
        color: '#3B82F6', weight: 2, dashArray: '6 6', opacity: 0.85,
      }).addTo(map)
    }
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
    // dblclick's two click events both land ~same spot — drop the duplicate vertex
    if (pts.length >= 2) {
      const [lat1, lng1] = pts[pts.length - 1]
      const [lat2, lng2] = pts[pts.length - 2]
      if (Math.abs(lat1 - lat2) < 1e-9 && Math.abs(lng1 - lng2) < 1e-9) pts.pop()
    }
    clearDraw(map)
    if (pts.length < 3) return

    if (resultLayerRef.current) { map.removeLayer(resultLayerRef.current); resultLayerRef.current = null }
    resultLayerRef.current = L.polygon(pts, {
      color: '#3B82F6', weight: 2, fillColor: '#3B82F6', fillOpacity: 0.2,
    }).addTo(map)

    // GeoJSON ring is [lng,lat] and must be closed
    const ring = pts.map(([lat, lng]) => [lng, lat])
    ring.push(ring[0])
    onPolygonDrawnRef.current?.({ type: 'Polygon', coordinates: [ring] })
  }

  function clearResultPolygon(map) {
    if (resultLayerRef.current) {
      map.removeLayer(resultLayerRef.current)
      resultLayerRef.current = null
    }
    editMarkersRef.current.forEach((marker) => map.removeLayer(marker))
    editMarkersRef.current = []
  }

  function renderResultPolygon(map, geometry, editable) {
    clearResultPolygon(map)
    if (geometry?.type !== 'Polygon' || !Array.isArray(geometry.coordinates?.[0])) return

    const rings = geometry.coordinates.map((ring) => {
      const coordinates = ring.length > 1
        && ring[0][0] === ring[ring.length - 1][0]
        && ring[0][1] === ring[ring.length - 1][1]
        ? ring.slice(0, -1)
        : ring
      return coordinates.map(([lng, lat]) => [lat, lng])
    })
    if (rings[0].length < 3) return

    resultLayerRef.current = L.polygon(rings, {
      color: editable ? '#F97316' : '#3B82F6',
      weight: editable ? 3 : 2,
      dashArray: editable ? '7 5' : null,
      fillColor: '#3B82F6',
      fillOpacity: 0.2,
    }).addTo(map)

    if (!editable) return
    rings[0].forEach((latLng, index) => {
      const marker = L.marker(latLng, {
        draggable: true,
        autoPan: true,
        keyboard: true,
        title: t('map.vertex', { number: index + 1 }),
        icon: L.divIcon({
          className: 'zone-edit-handle-wrap',
          html: '<span class="zone-edit-handle"></span>',
          iconSize: [18, 18],
          iconAnchor: [9, 9],
        }),
        zIndexOffset: 1200,
      }).addTo(map)
      marker.on('drag', (event) => {
        const { lat, lng } = event.target.getLatLng()
        rings[0][index] = [lat, lng]
        resultLayerRef.current?.setLatLngs(rings)
      })
      marker.on('dragend', () => {
        const editedRings = rings.map((ring, ringIndex) => {
          const points = ringIndex === 0
            ? editMarkersRef.current.map((handle) => handle.getLatLng())
            : ring.map(([lat, lng]) => ({ lat, lng }))
          const coordinates = points.map(({ lat, lng }) => [lng, lat])
          coordinates.push([...coordinates[0]])
          return coordinates
        })
        onPolygonEditedRef.current?.({ type: 'Polygon', coordinates: editedRings })
      })
      editMarkersRef.current.push(marker)
    })
  }

  function addLinePoint(map, lat, lng) {
    const pts = linePointsRef.current
    pts.push([lat, lng])
    const isFirst = pts.length === 1
    const m = L.circleMarker([lat, lng], {
      radius: isFirst ? 6 : 4, color: '#3B82F6', weight: 2, fillColor: isFirst ? '#FFFFFF' : '#3B82F6', fillOpacity: 1,
      dashArray: '4 3',
    }).addTo(map)
    lineMarkersRef.current.push(m)

    if (lineLayerRef.current) map.removeLayer(lineLayerRef.current)
    if (pts.length > 1) {
      lineLayerRef.current = L.polyline(pts, { color: '#3B82F6', weight: 3, dashArray: '8 6' }).addTo(map)
    }
    onLineDrawPointsChangeRef.current?.(pts.length)
  }

  function updateLinePreview(map, latlng) {
    const last = linePointsRef.current[linePointsRef.current.length - 1]
    if (!last) return
    const path = [last, [latlng.lat, latlng.lng]]
    if (linePreviewRef.current) {
      linePreviewRef.current.setLatLngs(path)
    } else {
      linePreviewRef.current = L.polyline(path, {
        color: '#3B82F6', weight: 2, dashArray: '4 4', opacity: 0.7,
      }).addTo(map)
    }
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
          renderer: L.svg(),   // className+CSS filter only apply on the SVG renderer, not Canvas
          style: {
            color: '#ffffff', weight: 2, opacity: 0.9, fill: false,
            lineCap: 'round', lineJoin: 'round',
          },
          interactive: false,
          className: 'boundary-glow',
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
            renderer: L.svg(),
            color: '#ffffff', weight: 2, fill: false, opacity: 0.9,
            lineCap: 'round', lineJoin: 'round', className: 'boundary-glow',
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

    // The satellite choice uses the app's measured B04/B03/B02 true-colour tiles.
    const showForecast = forecastMode && !!forecastYear
    const dataLayer = activeLayer === 'satellite' ? 'rgb' : activeLayer
    const dataVersion = periods.find((item) => item.period_id === period)?.data_version || ''
    const key = showForecast
      ? `forecast:${activeLayer}:${forecastYear}`
      : `${dataLayer}:${period}:${dataVersion}`
    Object.entries(overlaysRef.current).forEach(([id, layer]) => {
      if (id === key) return
      map.removeLayer(layer)
      delete overlaysRef.current[id]
    })
    if (!overlaysRef.current[key]) {
      const common = { opacity, tileSize: 256, minZoom: 4, maxZoom: 18, crossOrigin: true }
      const url = showForecast
        ? forecastTileUrl(activeLayer, forecastYear)
        : tileUrl(dataLayer, period, dataVersion)
      const layer = (clipGeo && L.TileLayer.boundaryCanvas)
        ? L.TileLayer.boundaryCanvas(url, { ...common, boundary: clipGeo })
        : L.tileLayer(url, common)
      layer.addTo(map)
      overlaysRef.current[key] = layer
      boundaryRef.current?.bringToFront?.()   // keep outline above the data
    }
    overlaysRef.current[key].setOpacity(opacity)
  }, [activeLayer, period, periods, opacity, clipGeo, forecastMode, forecastYear])

  // change-detection overlay — independent of the normal index layer above,
  // stacked on top of it at a fixed opacity rather than replacing it
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (clipGeo === undefined) return

    const periodsValid = changePeriodBefore && changePeriodAfter && changePeriodBefore !== changePeriodAfter
    if (!changeMode || !changeIndex || !periodsValid) {
      if (changeOverlayRef.current) {
        map.removeLayer(changeOverlayRef.current)
        changeOverlayRef.current = null
        changeOverlayKeyRef.current = null
      }
      return
    }

    const key = `change:${changeIndex}:${changePeriodBefore}:${changePeriodAfter}`
    if (changeOverlayKeyRef.current !== key) {
      if (changeOverlayRef.current) map.removeLayer(changeOverlayRef.current)
      const common = { opacity: CHANGE_OVERLAY_OPACITY, tileSize: 256, minZoom: 4, maxZoom: 18, crossOrigin: true }
      const url = changeTileUrl(changeIndex, changePeriodBefore, changePeriodAfter)
      const layer = (clipGeo && L.TileLayer.boundaryCanvas)
        ? L.TileLayer.boundaryCanvas(url, { ...common, boundary: clipGeo })
        : L.tileLayer(url, common)
      layer.addTo(map)
      boundaryRef.current?.bringToFront?.()
      changeOverlayRef.current = layer
      changeOverlayKeyRef.current = key
    }
  }, [changeMode, changeIndex, changePeriodBefore, changePeriodAfter, clipGeo])

  function zoomIn()  { mapRef.current?.zoomIn() }
  function zoomOut() { mapRef.current?.zoomOut() }
  function fit() {
    const map = mapRef.current
    if (!map) return
    if (boundaryRef.current?.getBounds) { map.fitBounds(boundaryRef.current.getBounds(), { padding: [16, 16] }); return }
    const b = boundsRef.current
    if (b) map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: [20, 20] })
  }
  function toggleLabels() {
    setLabelsOn((on) => {
      const next = !on
      labelsRef.current?.setOpacity(next ? 1 : 0)
      return next
    })
  }
  function toggleBasemap() {
    const idx = BASEMAP_ORDER.indexOf(basemapKind.current)
    const next = BASEMAP_ORDER[(idx + 1) % BASEMAP_ORDER.length]
    basemapKind.current = next
    basemapRef.current.setUrl(BASEMAPS[next].url)
    setBasemapKey(next)
  }

  const basemapName = t(BASEMAPS[basemapKey].name)

  return (
    <section className="map-section" id="map-workspace" tabIndex={-1} aria-label={t('map.aria')}>
      <div ref={elRef} id="map" />

      <div className="map-toolbar" role="toolbar" aria-label={t('map.toolbar')}>
        <button type="button" className="map-tool-btn" title={t('map.zoomIn')} aria-label={t('map.zoomIn')} onClick={zoomIn}>+</button>
        <button type="button" className="map-tool-btn" title={t('map.zoomOut')} aria-label={t('map.zoomOut')} onClick={zoomOut}>−</button>
        <div className="map-tool-sep" />
        <button type="button" className="map-tool-btn" title={t('map.fit')} aria-label={t('map.fit')} onClick={fit}>⊕</button>
        <button type="button" className="map-tool-btn" title={t('map.basemapTitle', { name: basemapName })} aria-label={t('map.basemapAria', { name: basemapName })} onClick={toggleBasemap}>⊞</button>
        <div className="map-tool-sep" />
        <button
          type="button"
          className="map-tool-btn"
          title={labelsOn ? t('map.hideLabels') : t('map.showLabels')}
          aria-label={labelsOn ? t('map.hideLabels') : t('map.showLabels')}
          aria-pressed={labelsOn}
          onClick={toggleLabels}
        >
          {labelsOn ? 'Abc' : 'abc'}
        </button>
      </div>

      <div className="map-footer">
        <span>Zoom: {zoomLevel}</span>
        <span className="map-footer-sep">·</span>
        <span>EPSG:32641</span>
        <span className="map-footer-sep">·</span>
        {forecastMode && (
          <>
            <span>{t('map.forecast', { year: forecastYear })}</span>
            <span className="map-footer-sep">·</span>
          </>
        )}
        <span>{hover ? `${hover.lat.toFixed(5)}°N · ${hover.lng.toFixed(5)}°E` : t('map.hover')}</span>
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
