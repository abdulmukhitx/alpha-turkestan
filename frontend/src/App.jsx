import { useEffect, useRef, useState } from 'react'
import TopBar from './components/TopBar.jsx'
import LayerPanel from './components/LayerPanel.jsx'
import MapView from './components/MapView.jsx'
import AnalysisPanel from './components/AnalysisPanel.jsx'
import ChangeDetectionBar from './components/ChangeDetectionBar.jsx'
import SplitMapView from './components/SplitMapView.jsx'
import { fetchHealth, fetchMetadata, fetchPeriods, fetchPixel, fetchAnalysis, fetchZoneStats, fetchTransect, fetchChangeStats } from './api'

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
  const [periods, setPeriods] = useState([])
  const [period, setPeriod] = useState('2025_summer')
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

  const [lineDrawMode, setLineDrawMode] = useState(false)
  const [transectLine, setTransectLine] = useState(null)
  const [transectData, setTransectData] = useState(null)
  const [transectLoading, setTransectLoading] = useState(false)
  const [transectError, setTransectError] = useState(null)
  const [lineClearSignal, setLineClearSignal] = useState(0)
  const [lineFinishSignal, setLineFinishSignal] = useState(0)
  const [lineDrawPointCount, setLineDrawPointCount] = useState(0)

  // Change detection — overlaid on top of the normal index layer (not instead
  // of it), toggled from a map-toolbar button. Reuses the same polygon-draw
  // plumbing as the regular zone tool (drawMode, clearSignal, finishSignal,
  // drawPointCount below); drawIntent decides which endpoint a just-finished
  // polygon feeds.
  const [changeMode, setChangeMode] = useState(false)
  const [changeIndex, setChangeIndex] = useState('ndvi')
  const [changePeriodBefore, setChangePeriodBefore] = useState(null)
  const [changePeriodAfter, setChangePeriodAfter] = useState(null)
  const [changeStats, setChangeStats] = useState(null)
  const [changeLoading, setChangeLoading] = useState(false)
  const [changeError, setChangeError] = useState(null)
  const [drawIntent, setDrawIntent] = useState('zone')

  // Split-screen comparison — mutually exclusive with change detection.
  // Left/right period+index default once periods load: earliest->NDVI, latest->NDVI.
  const [splitMode, setSplitMode] = useState(false)
  const [splitLeftPeriod, setSplitLeftPeriod] = useState(null)
  const [splitRightPeriod, setSplitRightPeriod] = useState(null)
  const [splitLeftIndex, setSplitLeftIndex] = useState('ndvi')
  const [splitRightIndex, setSplitRightIndex] = useState('ndvi')

  const requestIdRef = useRef(0)
  const zoneReqIdRef = useRef(0)
  const transectReqIdRef = useRef(0)
  const changeReqIdRef = useRef(0)

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
    fetchPeriods().then(setPeriods).catch(() => setPeriods([]))
  }, [])

  // default change-detection period pick: earliest -> latest, once periods load
  useEffect(() => {
    if (!periods.length || changePeriodBefore || changePeriodAfter) return
    setChangePeriodBefore(periods[0].period_id)
    setChangePeriodAfter(periods[periods.length - 1].period_id)
  }, [periods, changePeriodBefore, changePeriodAfter])

  // default split-screen period pick: earliest -> latest, once periods load
  useEffect(() => {
    if (!periods.length || splitLeftPeriod || splitRightPeriod) return
    setSplitLeftPeriod(periods[0].period_id)
    setSplitRightPeriod(periods[periods.length - 1].period_id)
  }, [periods, splitLeftPeriod, splitRightPeriod])

  function toggleSplitMode() {
    setSplitMode((s) => {
      const next = !s
      if (next) setChangeMode(false)
      return next
    })
  }

  function toggleChangeMode() {
    setChangeMode((m) => {
      const next = !m
      if (next) setSplitMode(false)
      return next
    })
  }

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
      const px = await fetchPixel(lat, lng, period)
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

  async function runZoneStats(geometry) {
    setZoneStats(null)
    setZoneError(null)
    setZoneLoading(true)

    const reqId = ++zoneReqIdRef.current
    try {
      const stats = await fetchZoneStats(geometry, period)
      if (zoneReqIdRef.current !== reqId) return
      setZoneStats(stats)
    } catch (e) {
      if (zoneReqIdRef.current !== reqId) return
      setZoneError(e.message || 'Не удалось получить статистику зоны')
    } finally {
      if (zoneReqIdRef.current === reqId) setZoneLoading(false)
    }
  }

  function handlePolygonDrawn(geometry) {
    setDrawMode(false)
    setZonePolygon(geometry)
    if (drawIntent === 'change') runChangeStats(geometry)
    else runZoneStats(geometry)
  }

  function handleClearZone() {
    zoneReqIdRef.current += 1   // invalidate any in-flight request
    changeReqIdRef.current += 1
    setZonePolygon(null)
    setZoneStats(null)
    setZoneError(null)
    setZoneLoading(false)
    setChangeStats(null)
    setChangeError(null)
    setChangeLoading(false)
    setDrawMode(false)
    setDrawPointCount(0)
    setClearSignal((n) => n + 1)
  }

  function toggleZoneDraw() {
    setDrawMode((d) => {
      if (!d) setDrawIntent('zone')
      return !d
    })
  }

  function toggleChangeDraw() {
    setDrawMode((d) => {
      if (!d) setDrawIntent('change')
      return !d
    })
  }

  async function runChangeStats(geometry) {
    setChangeStats(null)
    setChangeError(null)
    setChangeLoading(true)

    const reqId = ++changeReqIdRef.current
    try {
      const stats = await fetchChangeStats(geometry, changePeriodBefore, changePeriodAfter)
      if (changeReqIdRef.current !== reqId) return
      setChangeStats(stats)
    } catch (e) {
      if (changeReqIdRef.current !== reqId) return
      setChangeError(e.message || 'Не удалось получить статистику изменений')
    } finally {
      if (changeReqIdRef.current === reqId) setChangeLoading(false)
    }
  }

  async function runTransect(geometry, layer) {
    setTransectData(null)
    setTransectError(null)
    setTransectLoading(true)

    const reqId = ++transectReqIdRef.current
    try {
      const data = await fetchTransect(geometry, layer, period)
      if (transectReqIdRef.current !== reqId) return
      setTransectData(data)
    } catch (e) {
      if (transectReqIdRef.current !== reqId) return
      setTransectError(e.message || 'Не удалось получить профиль по линии')
    } finally {
      if (transectReqIdRef.current === reqId) setTransectLoading(false)
    }
  }

  function handleLineDrawn(geometry) {
    setLineDrawMode(false)
    setTransectLine(geometry)
    runTransect(geometry, activeLayer)
  }

  function handleClearLine() {
    transectReqIdRef.current += 1
    setTransectLine(null)
    setTransectData(null)
    setTransectError(null)
    setTransectLoading(false)
    setLineDrawMode(false)
    setLineDrawPointCount(0)
    setLineClearSignal((n) => n + 1)
  }

  // re-fetch the transect when the active layer changes while a line is already drawn
  useEffect(() => {
    if (!transectLine) return
    runTransect(transectLine, activeLayer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeLayer])

  // switching period keeps any drawn geometry as-is and just re-queries it —
  // each period is viewed independently, no comparison between them
  useEffect(() => {
    if (transectLine) runTransect(transectLine, activeLayer)
    if (zonePolygon) runZoneStats(zonePolygon)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period])

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
        periods={periods}
        period={period}
        onPeriodChange={setPeriod}
      />

      <main className={`workspace ${splitMode ? 'workspace-no-left' : ''}`}>
        {!splitMode && (
          <LayerPanel
            layers={layers}
            activeLayer={activeLayer}
            onSelect={setActiveLayer}
            opacity={opacity}
            onOpacityChange={setOpacity}
            drawMode={drawMode}
            onToggleDraw={toggleZoneDraw}
            onClearZone={handleClearZone}
            onFinishDraw={() => setFinishSignal((n) => n + 1)}
            hasZone={!!zonePolygon}
            drawPointCount={drawPointCount}
            lineDrawMode={lineDrawMode}
            onToggleLineDraw={() => setLineDrawMode((d) => !d)}
            onClearLine={handleClearLine}
            onFinishLineDraw={() => setLineFinishSignal((n) => n + 1)}
            hasLine={!!transectLine}
            lineDrawPointCount={lineDrawPointCount}
          />
        )}

        {splitMode ? (
          <SplitMapView
            periods={periods}
            bounds={meta?.region?.bounds}
            center={meta?.region?.center || FALLBACK_CENTER}
            zoom={FALLBACK_ZOOM}
            leftPeriod={splitLeftPeriod}
            leftIndex={splitLeftIndex}
            onLeftPeriodChange={setSplitLeftPeriod}
            onLeftIndexChange={setSplitLeftIndex}
            rightPeriod={splitRightPeriod}
            rightIndex={splitRightIndex}
            onRightPeriodChange={setSplitRightPeriod}
            onRightIndexChange={setSplitRightIndex}
            drawMode={drawMode}
            onPolygonDrawn={handlePolygonDrawn}
            clearSignal={clearSignal}
            finishSignal={finishSignal}
            onDrawPointsChange={setDrawPointCount}
            lineDrawMode={lineDrawMode}
            onLineDrawn={handleLineDrawn}
            lineClearSignal={lineClearSignal}
            lineFinishSignal={lineFinishSignal}
            onLineDrawPointsChange={setLineDrawPointCount}
            onPointClick={handlePointClick}
            onMouseMove={(lat, lng) => setHoverPos({ lat, lng })}
            onExitSplitMode={toggleSplitMode}
          />
        ) : (
          <MapView
            activeLayer={activeLayer}
            period={period}
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
            lineDrawMode={lineDrawMode}
            onLineDrawn={handleLineDrawn}
            lineClearSignal={lineClearSignal}
            lineFinishSignal={lineFinishSignal}
            onLineDrawPointsChange={setLineDrawPointCount}
            changeMode={changeMode}
            onToggleChangeMode={toggleChangeMode}
            changeIndex={changeIndex}
            changePeriodBefore={changePeriodBefore}
            changePeriodAfter={changePeriodAfter}
            splitMode={splitMode}
            onToggleSplitMode={toggleSplitMode}
          />
        )}

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
          transectData={transectData}
          transectLoading={transectLoading}
          transectError={transectError}
          changeStats={changeStats}
          changeLoading={changeLoading}
          changeError={changeError}
        />
      </main>

      <ChangeDetectionBar
        open={changeMode && !splitMode}
        periods={periods}
        periodBefore={changePeriodBefore}
        periodAfter={changePeriodAfter}
        onPeriodBeforeChange={setChangePeriodBefore}
        onPeriodAfterChange={setChangePeriodAfter}
        index={changeIndex}
        onIndexChange={setChangeIndex}
        drawMode={drawMode}
        onToggleDraw={toggleChangeDraw}
        onFinishDraw={() => setFinishSignal((n) => n + 1)}
        onClearZone={handleClearZone}
        hasZone={!!zonePolygon}
        drawPointCount={drawPointCount}
      />
    </div>
  )
}
