import { useEffect, useRef, useState } from 'react'
import TopBar from './components/TopBar.jsx'
import LayerPanel from './components/LayerPanel.jsx'
import MapView from './components/MapView.jsx'
import AnalysisPanel from './components/AnalysisPanel.jsx'
import { fetchHealth, fetchMetadata, fetchPixel, fetchAnalysis, fetchZoneStats } from './api'

const FALLBACK_CENTER = [43.39, 68.36]
const FALLBACK_ZOOM = 7

const BOOT_STEPS = [
  [80,  'Подключение к серверу...'],
  [260, 'Загрузка геоданных...'],
  [460, 'Инициализация карты...'],
  [640, 'Система готова'],
]

export default function App() {
  const [booting, setBooting] = useState(true)
  const [bootFadeOut, setBootFadeOut] = useState(false)
  const [bootMsg, setBootMsg] = useState(BOOT_STEPS[0][1])
  const [bootPct, setBootPct] = useState(0)

  const [health, setHealth] = useState(null)
  const [meta, setMeta] = useState(null)
  const [activeLayer, setActiveLayer] = useState('ndvi')
  const [opacity, setOpacity] = useState(0.85)
  const [hoverPos, setHoverPos] = useState(null)

  const [point, setPixelPoint] = useState(null)
  const [pixel, setPixel] = useState(null)
  const [aiText, setAiText] = useState('')
  const [aiLoading, setAiLoading] = useState(false)
  const [aiError, setAiError] = useState(null)

  const [drawMode, setDrawMode] = useState(false)
  const [zonePolygon, setZonePolygon] = useState(null)
  const [zoneStats, setZoneStats] = useState(null)
  const [zoneLoading, setZoneLoading] = useState(false)
  const [zoneError, setZoneError] = useState(null)
  const [clearSignal, setClearSignal] = useState(0)
  const [finishSignal, setFinishSignal] = useState(0)
  const [drawPointCount, setDrawPointCount] = useState(0)

  const requestIdRef = useRef(0)
  const zoneReqIdRef = useRef(0)

  useEffect(() => {
    const total = BOOT_STEPS[BOOT_STEPS.length - 1][0]
    const timers = BOOT_STEPS.map(([ms, msg]) => setTimeout(() => {
      setBootMsg(msg)
      setBootPct(Math.round((ms / total) * 100))
    }, ms))
    timers.push(setTimeout(() => setBootFadeOut(true), total + 150))
    timers.push(setTimeout(() => setBooting(false), total + 650))
    return () => timers.forEach(clearTimeout)
  }, [])

  useEffect(() => {
    refreshHealth()
    fetchMetadata().then(setMeta).catch(() => setMeta(null))
  }, [])

  function refreshHealth() {
    fetchHealth().then(setHealth).catch(() => setHealth({ status: 'offline' }))
  }

  async function handlePointClick(lat, lng) {
    setPixelPoint({ lat, lng })
    setPixel(null)
    setAiText('')
    setAiError(null)
    setAiLoading(true)

    const reqId = ++requestIdRef.current
    try {
      const px = await fetchPixel(lat, lng)
      if (requestIdRef.current !== reqId) return
      setPixel(px)

      const analysis = await fetchAnalysis({
        lat, lon: lng,
        ndvi: px.ndvi, ndwi: px.ndwi, ndre: px.ndre, ndmi: px.ndmi, bsi: px.bsi, savi: px.savi, nbr: px.nbr,
        ml_class: px.ml_class, ml_class_ru: px.ml_class_ru, ml_confidence: px.ml_confidence,
      })
      if (requestIdRef.current !== reqId) return
      setAiText(analysis.analysis || 'Анализ недоступен')
    } catch {
      if (requestIdRef.current !== reqId) return
      setAiError('Не удалось получить данные для этой точки. Проверьте соединение с сервером (backend на :8000).')
    } finally {
      if (requestIdRef.current === reqId) setAiLoading(false)
    }
  }

  async function handlePolygonDrawn(geometry) {
    setDrawMode(false)
    setZonePolygon(geometry)
    setZoneStats(null)
    setZoneError(null)
    setZoneLoading(true)

    const reqId = ++zoneReqIdRef.current
    try {
      const stats = await fetchZoneStats(geometry)
      if (zoneReqIdRef.current !== reqId) return
      setZoneStats(stats)
    } catch (e) {
      if (zoneReqIdRef.current !== reqId) return
      setZoneError(e.message || 'Не удалось получить статистику зоны')
    } finally {
      if (zoneReqIdRef.current === reqId) setZoneLoading(false)
    }
  }

  function handleClearZone() {
    zoneReqIdRef.current += 1   // invalidate any in-flight request
    setZonePolygon(null)
    setZoneStats(null)
    setZoneError(null)
    setZoneLoading(false)
    setDrawMode(false)
    setDrawPointCount(0)
    setClearSignal((n) => n + 1)
  }

  const layers = {}
  if (meta?.layers) {
    for (const [id, cfg] of Object.entries(meta.layers)) {
      layers[id] = { ...cfg, cssGradient: meta.cmaps?.[id] }
    }
  }

  return (
    <div className="app-shell">
      {booting && (
        <div id="boot-screen" className={bootFadeOut ? 'fade-out' : ''}>
          <div className="boot-inner">
            <div className="boot-title">GeoAI Platform</div>
            <div className="boot-sub">Туркестанская область · Казахстан</div>
            <div className="boot-bar"><div className="boot-fill" style={{ width: `${bootPct}%` }} /></div>
            <div className="boot-status">{bootMsg}</div>
          </div>
        </div>
      )}

      <TopBar
        lat={hoverPos?.lat}
        lon={hoverPos?.lng}
        health={health}
        onRefresh={refreshHealth}
      />

      <main className="workspace">
        <LayerPanel
          layers={layers}
          activeLayer={activeLayer}
          onSelect={setActiveLayer}
          opacity={opacity}
          onOpacityChange={setOpacity}
          drawMode={drawMode}
          onToggleDraw={() => setDrawMode((d) => !d)}
          onClearZone={handleClearZone}
          onFinishDraw={() => setFinishSignal((n) => n + 1)}
          hasZone={!!zonePolygon}
          drawPointCount={drawPointCount}
        />

        <MapView
          activeLayer={activeLayer}
          opacity={opacity}
          bounds={meta?.region?.bounds}
          center={meta?.region?.center || FALLBACK_CENTER}
          zoom={FALLBACK_ZOOM}
          onPointClick={handlePointClick}
          onMouseMove={(lat, lng) => setHoverPos({ lat, lng })}
          onZoomChange={() => {}}
          drawMode={drawMode}
          onPolygonDrawn={handlePolygonDrawn}
          clearSignal={clearSignal}
          finishSignal={finishSignal}
          onDrawPointsChange={setDrawPointCount}
        />

        <AnalysisPanel
          point={point}
          pixel={pixel}
          aiText={aiText}
          loading={aiLoading}
          error={aiError}
          zoneStats={zoneStats}
          zoneLoading={zoneLoading}
          zoneError={zoneError}
          zoneGeometry={zonePolygon}
          activeLayer={activeLayer}
        />
      </main>
    </div>
  )
}
