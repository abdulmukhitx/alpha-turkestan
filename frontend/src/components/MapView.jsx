import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { tileUrl } from '../api'

const BASEMAPS = {
  light: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
  sat:   'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
}

export default function MapView({ activeLayer, opacity, bounds, center, zoom, onPointClick, onMouseMove, onZoomChange }) {
  const elRef       = useRef(null)
  const mapRef      = useRef(null)
  const basemapRef  = useRef(null)
  const basemapKind = useRef('light')
  const overlaysRef = useRef({})
  const markerRef   = useRef(null)
  const boundaryRef = useRef(null)
  const boundsRef   = useRef(bounds)
  const callbacksRef = useRef({ onPointClick, onMouseMove, onZoomChange })
  callbacksRef.current = { onPointClick, onMouseMove, onZoomChange }

  const [zoomLevel, setZoomLevel] = useState(zoom)
  const [hover, setHover] = useState(null)

  // init map once
  useEffect(() => {
    const map = L.map(elRef.current, {
      center, zoom, zoomControl: false, attributionControl: false, preferCanvas: true,
    })
    mapRef.current = map

    basemapRef.current = L.tileLayer(BASEMAPS.light, { maxZoom: 18, subdomains: 'abcd' }).addTo(map)

    map.on('click', (e) => {
      const { lat, lng } = e.latlng
      const b = boundsRef.current
      if (b && (lat < b[0] || lat > b[2] || lng < b[1] || lng > b[3])) return
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

  // fit + draw boundary once real bounds arrive from /metadata
  useEffect(() => {
    boundsRef.current = bounds
    const map = mapRef.current
    if (!map || !bounds) return
    const leafletBounds = [[bounds[0], bounds[1]], [bounds[2], bounds[3]]]
    if (boundaryRef.current) map.removeLayer(boundaryRef.current)
    boundaryRef.current = L.rectangle(leafletBounds, {
      color: '#16A34A', weight: 1.5, fill: false, opacity: 0.6, dashArray: '6 4',
    }).addTo(map)
    map.fitBounds(leafletBounds, { padding: [20, 20] })
  }, [bounds])

  // lazy-load + switch active layer
  useEffect(() => {
    const map = mapRef.current
    if (!map || !activeLayer) return
    if (!overlaysRef.current[activeLayer]) {
      const layer = L.tileLayer(tileUrl(activeLayer), {
        opacity, tileSize: 256, minZoom: 4, maxZoom: 18, crossOrigin: true,
      }).addTo(map)
      overlaysRef.current[activeLayer] = layer
    }
    Object.entries(overlaysRef.current).forEach(([id, layer]) => {
      layer.setOpacity(id === activeLayer ? opacity : 0)
    })
  }, [activeLayer, opacity])

  function zoomIn()  { mapRef.current?.zoomIn() }
  function zoomOut() { mapRef.current?.zoomOut() }
  function fit()     { if (boundsRef.current) mapRef.current?.fitBounds([[boundsRef.current[0], boundsRef.current[1]], [boundsRef.current[2], boundsRef.current[3]]], { padding: [20, 20] }) }
  function toggleBasemap() {
    basemapKind.current = basemapKind.current === 'light' ? 'sat' : 'light'
    basemapRef.current.setUrl(BASEMAPS[basemapKind.current])
  }

  return (
    <section className="map-section">
      <div ref={elRef} id="map" />

      <div className="map-toolbar">
        <button className="map-tool-btn" title="Приближение" onClick={zoomIn}>+</button>
        <button className="map-tool-btn" title="Отдаление" onClick={zoomOut}>−</button>
        <div className="map-tool-sep" />
        <button className="map-tool-btn" title="По всей области" onClick={fit}>⊕</button>
        <button className="map-tool-btn" title="Спутник / карта" onClick={toggleBasemap}>⊞</button>
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
