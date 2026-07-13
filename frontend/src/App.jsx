import { useEffect, useRef, useState } from 'react'
import TopBar from './components/TopBar.jsx'
import LayerPanel from './components/LayerPanel.jsx'
import MapView from './components/MapView.jsx'
import AnalysisPanel from './components/AnalysisPanel.jsx'
import ChangeDetectionBar from './components/ChangeDetectionBar.jsx'
import ForecastBar from './components/ForecastBar.jsx'
import SplitMapView from './components/SplitMapView.jsx'
import WorkspaceNav from './components/WorkspaceNav.jsx'
import { fetchHealth, fetchMetadata, fetchPeriods, fetchPixel, fetchAnalysis, fetchZoneStats, fetchTransect, fetchChangeStats, fetchPointForecast } from './api'

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
  const [leftPanelOpen, setLeftPanelOpen] = useState(true)
  const [rightPanelOpen, setRightPanelOpen] = useState(false)

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
  const [zoneContext, setZoneContext] = useState({ period: '2025_summer', layer: 'ndvi', pane: 'main' })
  const [clearSignal, setClearSignal] = useState(0)
  const [finishSignal, setFinishSignal] = useState(0)
  const [drawPointCount, setDrawPointCount] = useState(0)

  const [lineDrawMode, setLineDrawMode] = useState(false)
  const [transectLine, setTransectLine] = useState(null)
  const [transectData, setTransectData] = useState(null)
  const [transectLoading, setTransectLoading] = useState(false)
  const [transectError, setTransectError] = useState(null)
  const [transectContext, setTransectContext] = useState({ period: '2025_summer', layer: 'ndvi', pane: 'main' })
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
  const [changePolygon, setChangePolygon] = useState(null)
  const [drawIntent, setDrawIntent] = useState('zone')

  // Split-screen comparison — mutually exclusive with change detection.
  // Left/right period+index default once periods load: earliest->NDVI, latest->NDVI.
  const [splitMode, setSplitMode] = useState(false)
  const [splitLeftPeriod, setSplitLeftPeriod] = useState(null)
  const [splitRightPeriod, setSplitRightPeriod] = useState(null)
  const [splitLeftIndex, setSplitLeftIndex] = useState('ndvi')
  const [splitRightIndex, setSplitRightIndex] = useState('ndvi')

  // Experimental baseline forecast. It is intentionally separate from the
  // current LULC classifier and can later be replaced by forecast COGs/model
  // endpoints without changing the UI contract.
  const [forecastMode, setForecastMode] = useState(false)
  const [forecastYear, setForecastYear] = useState(2026)
  const [forecastResult, setForecastResult] = useState(null)
  const [forecastLoading, setForecastLoading] = useState(false)
  const [forecastError, setForecastError] = useState(null)

  const requestIdRef = useRef(0)
  const zoneReqIdRef = useRef(0)
  const transectReqIdRef = useRef(0)
  const changeReqIdRef = useRef(0)
  const forecastReqIdRef = useRef(0)

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
    refreshData()
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

  useEffect(() => {
    const minYear = meta?.forecast?.min_target_year
    const maxYear = meta?.forecast?.max_target_year
    if (!minYear || !maxYear) return
    setForecastYear((year) => Math.max(minYear, Math.min(maxYear, year)))
  }, [meta])

  function clearForecastPoint() {
    forecastReqIdRef.current += 1
    setForecastResult(null)
    setForecastError(null)
    setForecastLoading(false)
    setPixelPoint(null)
    setPixel(null)
    setAiText('')
    setAiError(null)
    setAiLoading(false)
  }

  function toggleSplitMode() {
    const next = !splitMode
    setSplitMode(next)
    if (next) {
      setChangeMode(false)
      setForecastMode(false)
      setDrawMode(false)
      setLineDrawMode(false)
      if (forecastMode) clearForecastPoint()
    }
  }

  function toggleChangeMode() {
    const next = !changeMode
    setChangeMode(next)
    if (next) {
      setSplitMode(false)
      setForecastMode(false)
      setLineDrawMode(false)
      if (forecastMode) clearForecastPoint()
    }
  }

  function toggleForecastMode() {
    if (!meta?.forecast?.enabled) return
    const next = !forecastMode
    setForecastMode(next)
    setSplitMode(false)
    setChangeMode(false)
    setDrawMode(false)
    setLineDrawMode(false)
    clearForecastPoint()
    if (next && activeLayer === 'satellite') setActiveLayer('ndvi')
  }

  function activateWorkspaceMode(nextMode) {
    if (nextMode === 'overview') {
      if (splitMode) toggleSplitMode()
      else if (changeMode) toggleChangeMode()
      else if (forecastMode) toggleForecastMode()
      return
    }
    if (nextMode === 'compare' && !splitMode) toggleSplitMode()
    if (nextMode === 'change' && !changeMode) toggleChangeMode()
    if (nextMode === 'forecast' && !forecastMode && meta?.forecast?.enabled) {
      toggleForecastMode()
      setRightPanelOpen(true)
    }
  }

  function refreshData() {
    fetchHealth().then(setHealth).catch(() => setHealth({ status: 'offline' }))
    fetchMetadata().then(setMeta).catch(() => setMeta(null))
    fetchPeriods()
      .then((items) => setPeriods(items.filter((item) => item.available !== false)))
      .catch(() => setPeriods([]))
  }

  async function runForecastPoint(lat, lng, index = activeLayer, targetYear = forecastYear) {
    const forecastIndex = index === 'satellite' ? 'ndvi' : index
    setPixelPoint({ lat, lng, period: `forecast_${targetYear}`, layer: forecastIndex, pane: 'main' })
    setForecastResult(null)
    setForecastError(null)
    setForecastLoading(true)

    const reqId = ++forecastReqIdRef.current
    try {
      const result = await fetchPointForecast(lat, lng, forecastIndex, targetYear)
      if (forecastReqIdRef.current !== reqId) return
      setForecastResult(result)
    } catch (e) {
      if (forecastReqIdRef.current !== reqId) return
      setForecastError(e.message || 'Не удалось рассчитать линейный прогноз')
    } finally {
      if (forecastReqIdRef.current === reqId) setForecastLoading(false)
    }
  }

  async function handlePointClick(lat, lng, context = {}) {
    const target = {
      period: context.period || period,
      layer: context.layer || activeLayer,
      pane: context.pane || 'main',
    }
    setRightPanelOpen(true)
    if (forecastMode && target.pane === 'main') {
      runForecastPoint(lat, lng, target.layer, forecastYear)
      return
    }
    setPixelPoint({ lat, lng, ...target })
    setPixel(null)
    setAiText('')
    setAiError(null)
    setAiLoading(true)

    const reqId = ++requestIdRef.current
    let px
    try {
      px = await fetchPixel(lat, lng, target.period)
      if (requestIdRef.current !== reqId) return
      setPixel(px)
    } catch (e) {
      if (requestIdRef.current !== reqId) return
      setAiError(e.message || 'Не удалось получить спутниковые данные для этой точки')
      setAiLoading(false)
      return
    }

    if (px.demo) {
      setAiError('Показаны демонстрационные данные — они не являются спутниковыми измерениями.')
      setAiLoading(false)
      return
    }

    try {
      const analysis = await fetchAnalysis({
        lat, lon: lng, period: target.period,
        ndvi: px.ndvi, ndwi: px.ndwi, ndre: px.ndre, ndmi: px.ndmi, bsi: px.bsi, savi: px.savi, nbr: px.nbr,
        ml_class: px.ml_class, ml_class_ru: px.ml_class_ru, ml_confidence: px.ml_confidence,
      })
      if (requestIdRef.current !== reqId) return
      setAiText(analysis.analysis || 'Анализ недоступен')
    } catch (e) {
      if (requestIdRef.current !== reqId) return
      setAiError(`Спутниковые индексы получены, но AI-анализ недоступен: ${e.message || 'ошибка сервиса'}`)
    } finally {
      if (requestIdRef.current === reqId) setAiLoading(false)
    }
  }

  async function runZoneStats(geometry, context = {}) {
    const target = {
      period: context.period || period,
      layer: context.layer || activeLayer,
      pane: context.pane || 'main',
    }
    setRightPanelOpen(true)
    setZoneContext(target)
    setZoneStats(null)
    setZoneError(null)
    setZoneLoading(true)

    const reqId = ++zoneReqIdRef.current
    try {
      const stats = await fetchZoneStats(geometry, target.period)
      if (zoneReqIdRef.current !== reqId) return
      setZoneStats(stats)
    } catch (e) {
      if (zoneReqIdRef.current !== reqId) return
      setZoneError(e.message || 'Не удалось получить статистику зоны')
    } finally {
      if (zoneReqIdRef.current === reqId) setZoneLoading(false)
    }
  }

  function handlePolygonDrawn(geometry, context = {}) {
    setDrawMode(false)
    if (drawIntent === 'change') {
      zoneReqIdRef.current += 1
      setZonePolygon(null)
      setZoneStats(null)
      setZoneError(null)
      setChangePolygon(geometry)
      runChangeStats(geometry, changePeriodBefore, changePeriodAfter)
    } else {
      changeReqIdRef.current += 1
      setChangePolygon(null)
      setChangeStats(null)
      setChangeError(null)
      setZonePolygon(geometry)
      runZoneStats(geometry, context)
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

  function handleClearChange() {
    changeReqIdRef.current += 1
    setChangePolygon(null)
    setChangeStats(null)
    setChangeError(null)
    setChangeLoading(false)
    setDrawMode(false)
    setDrawPointCount(0)
    setClearSignal((n) => n + 1)
  }

  function toggleZoneDraw() {
    setDrawMode((d) => {
      if (!d) {
        setDrawIntent('zone')
        setLineDrawMode(false)
      }
      return !d
    })
  }

  function toggleChangeDraw() {
    setDrawMode((d) => {
      if (!d) {
        setDrawIntent('change')
        setLineDrawMode(false)
      }
      return !d
    })
  }

  async function runChangeStats(geometry, periodBefore = changePeriodBefore, periodAfter = changePeriodAfter) {
    setRightPanelOpen(true)
    if (!periodBefore || !periodAfter || periodBefore === periodAfter) {
      setChangeStats(null)
      setChangeError(periodBefore === periodAfter ? 'Выберите разные периоды' : null)
      setChangeLoading(false)
      return
    }
    setChangeStats(null)
    setChangeError(null)
    setChangeLoading(true)

    const reqId = ++changeReqIdRef.current
    try {
      const stats = await fetchChangeStats(geometry, periodBefore, periodAfter)
      if (changeReqIdRef.current !== reqId) return
      setChangeStats(stats)
    } catch (e) {
      if (changeReqIdRef.current !== reqId) return
      setChangeError(e.message || 'Не удалось получить статистику изменений')
    } finally {
      if (changeReqIdRef.current === reqId) setChangeLoading(false)
    }
  }

  async function runTransect(geometry, context = {}) {
    const target = {
      period: context.period || period,
      layer: context.layer || activeLayer,
      pane: context.pane || 'main',
    }
    setRightPanelOpen(true)
    setTransectContext(target)
    setTransectData(null)
    setTransectError(null)
    if (target.layer === 'satellite') {
      setTransectError('Для профиля выберите спектральный индекс, а не спутниковый снимок.')
      setTransectLoading(false)
      return
    }
    setTransectLoading(true)

    const reqId = ++transectReqIdRef.current
    try {
      const data = await fetchTransect(geometry, target.layer, target.period)
      if (transectReqIdRef.current !== reqId) return
      setTransectData(data)
    } catch (e) {
      if (transectReqIdRef.current !== reqId) return
      setTransectError(e.message || 'Не удалось получить профиль по линии')
    } finally {
      if (transectReqIdRef.current === reqId) setTransectLoading(false)
    }
  }

  function handleLineDrawn(geometry, context = {}) {
    setLineDrawMode(false)
    setTransectLine(geometry)
    runTransect(geometry, context)
  }

  function toggleLineDraw() {
    if (activeLayer === 'satellite') {
      setTransectError('Для профиля выберите спектральный индекс.')
      return
    }
    setLineDrawMode((drawing) => {
      if (!drawing) setDrawMode(false)
      return !drawing
    })
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
    if (!transectLine || splitMode || forecastMode) return
    runTransect(transectLine, { period, layer: activeLayer, pane: 'main' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeLayer])

  // switching period keeps any drawn geometry as-is and just re-queries it —
  // each period is viewed independently, no comparison between them
  useEffect(() => {
    if (splitMode || forecastMode) return
    if (point?.pane === 'main') handlePointClick(point.lat, point.lng, { period, layer: activeLayer, pane: 'main' })
    if (transectLine) runTransect(transectLine, { period, layer: activeLayer, pane: 'main' })
    if (zonePolygon) runZoneStats(zonePolygon, { period, layer: activeLayer, pane: 'main' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period])

  useEffect(() => {
    if (!forecastMode || !point || point.pane !== 'main') return
    runForecastPoint(point.lat, point.lng, activeLayer, forecastYear)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [forecastYear, activeLayer])

  useEffect(() => {
    if (!changePolygon) return
    runChangeStats(changePolygon, changePeriodBefore, changePeriodAfter)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [changePeriodBefore, changePeriodAfter])

  useEffect(() => {
    if (!splitMode || !splitLeftPeriod) return
    const context = { period: splitLeftPeriod, layer: splitLeftIndex, pane: 'left' }
    if (point?.pane === 'left') handlePointClick(point.lat, point.lng, context)
    if (zonePolygon && zoneContext.pane === 'left') runZoneStats(zonePolygon, context)
    if (transectLine && transectContext.pane === 'left') runTransect(transectLine, context)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [splitLeftPeriod])

  useEffect(() => {
    if (!splitMode || !transectLine || transectContext.pane !== 'left') return
    runTransect(transectLine, { period: splitLeftPeriod, layer: splitLeftIndex, pane: 'left' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [splitLeftIndex])

  useEffect(() => {
    if (!splitMode || !splitRightPeriod || point?.pane !== 'right') return
    handlePointClick(point.lat, point.lng, { period: splitRightPeriod, layer: splitRightIndex, pane: 'right' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [splitRightPeriod])

  useEffect(() => {
    if (splitMode) return
    const context = { period, layer: activeLayer, pane: 'main' }
    if (point && point.pane !== 'main') handlePointClick(point.lat, point.lng, context)
    if (zonePolygon && zoneContext.pane !== 'main') runZoneStats(zonePolygon, context)
    if (transectLine && transectContext.pane !== 'main') runTransect(transectLine, context)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [splitMode])

  const layers = {}
  if (meta?.layers) {
    for (const [id, cfg] of Object.entries(meta.layers)) {
      layers[id] = { ...cfg, cssGradient: meta.cmaps?.[id] }
    }
  }

  const workspaceMode = splitMode ? 'compare' : changeMode ? 'change' : forecastMode ? 'forecast' : 'overview'
  const hasResults = !!(
    point || pixel || aiText || aiError || aiLoading ||
    zoneStats || zoneLoading || zoneError ||
    transectData || transectLoading || transectError ||
    changeStats || changeLoading || changeError ||
    forecastResult || forecastLoading || forecastError
  )
  const showLeftPanel = !splitMode && leftPanelOpen
  const showRightPanel = rightPanelOpen
  const workspaceClass = [
    'workspace',
    !showLeftPanel && 'workspace-no-left',
    !showRightPanel && 'workspace-no-right',
    !showLeftPanel && !showRightPanel && 'workspace-map-only',
  ].filter(Boolean).join(' ')
  const shellInsets = {
    '--active-left-inset': showLeftPanel ? 'var(--panel-left-w)' : '0px',
    '--active-right-inset': showRightPanel ? 'var(--panel-right-w)' : '0px',
  }

  return (
    <div className="app-shell" style={shellInsets}>
      <a className="skip-link" href="#map-workspace">Перейти к карте</a>
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
        onRefresh={refreshData}
        periods={periods}
        period={period}
        onPeriodChange={setPeriod}
        periodDisabled={forecastMode}
      />

      <WorkspaceNav
        activeMode={workspaceMode}
        onModeChange={activateWorkspaceMode}
        forecastAvailable={meta?.forecast?.enabled === true}
        leftPanelAvailable={!splitMode}
        leftPanelOpen={showLeftPanel}
        onToggleLeftPanel={() => setLeftPanelOpen((open) => !open)}
        rightPanelOpen={showRightPanel}
        onToggleRightPanel={() => setRightPanelOpen((open) => !open)}
        hasResults={hasResults}
      />

      <main className={workspaceClass}>
        {showLeftPanel && (
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
            onToggleLineDraw={toggleLineDraw}
            lineDisabled={activeLayer === 'satellite'}
            onClearLine={handleClearLine}
            onFinishLineDraw={() => setLineFinishSignal((n) => n + 1)}
            hasLine={!!transectLine}
            lineDrawPointCount={lineDrawPointCount}
            forecastMode={forecastMode}
            onClose={() => setLeftPanelOpen(false)}
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
            changeIndex={changeIndex}
            changePeriodBefore={changePeriodBefore}
            changePeriodAfter={changePeriodAfter}
            forecastMode={forecastMode}
            forecastYear={forecastYear}
          />
        )}

        {showRightPanel && (
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
            activeLayer={zoneContext.layer}
            zonePeriod={zoneContext.period}
            transectData={transectData}
            transectLoading={transectLoading}
            transectError={transectError}
            changeStats={changeMode && !splitMode ? changeStats : null}
            changeLoading={changeMode && !splitMode ? changeLoading : false}
            changeError={changeMode && !splitMode ? changeError : null}
            forecastMode={forecastMode && !splitMode}
            forecastResult={forecastResult}
            forecastLoading={forecastLoading}
            forecastError={forecastError}
            forecastYear={forecastYear}
            forecastIndex={activeLayer}
            onClose={() => setRightPanelOpen(false)}
          />
        )}
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
        onClearZone={handleClearChange}
        hasZone={!!changePolygon}
        drawPointCount={drawPointCount}
      />

      <ForecastBar
        open={forecastMode && !splitMode}
        config={meta?.forecast}
        targetYear={forecastYear}
        onTargetYearChange={setForecastYear}
        activeIndex={activeLayer}
      />
    </div>
  )
}
