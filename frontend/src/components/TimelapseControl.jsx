import { useEffect, useMemo, useState } from 'react'
import { fetchTimelapseSceneFrame, searchTimelapseScenes, tileUrl } from '../api.js'
import { useI18n } from '../i18n.jsx'
import {
  geometryBounds, nextFrameIndex, padBounds, prepareAoiFrame, prepareCenterFrame,
  prepareSceneFrameBlob, previewTileUrl, tileGridForBounds, uniqueSceneAcquisitions,
} from '../timelapse.js'

const SPEED_OPTIONS = [0.5, 1, 2, 4]
const MIN_SCENE_COVERAGE = 90

export default function TimelapseControl({
  open, periods = [], period, onPeriodChange, activeLayer = 'satellite', center = [43.3, 68.25],
  zoneGeometry = null,
}) {
  const { t, periodLabel, formatDate } = useI18n()
  const [playing, setPlaying] = useState(false)
  const [studioOpen, setStudioOpen] = useState(false)
  const [playheadId, setPlayheadId] = useState(period)
  const [studioMode, setStudioMode] = useState('mosaics')
  const [fps, setFps] = useState(1)
  const [loop, setLoop] = useState(true)
  const [transition, setTransition] = useState('fade')
  const [selectedIds, setSelectedIds] = useState([])
  const initialSceneYear = String(period || '').match(/^\d{4}/)?.[0] || new Date().getUTCFullYear()
  const [sceneStartDate, setSceneStartDate] = useState(`${initialSceneYear}-06-01`)
  const [sceneEndDate, setSceneEndDate] = useState(`${initialSceneYear}-08-31`)
  const [maxCloudCover, setMaxCloudCover] = useState(30)
  const [sceneResult, setSceneResult] = useState(null)
  const [selectedSceneIds, setSelectedSceneIds] = useState([])
  const [scenePlaybackIds, setScenePlaybackIds] = useState([])
  const [sceneLoading, setSceneLoading] = useState(false)
  const [sceneError, setSceneError] = useState('')
  const [skippedSceneCount, setSkippedSceneCount] = useState(0)
  const [preloadRequested, setPreloadRequested] = useState(false)
  const [preloadRetry, setPreloadRetry] = useState(0)
  const [preload, setPreload] = useState({ status: 'idle', loaded: 0, total: 0, frames: {}, scope: 'center', error: '', key: '' })
  const available = useMemo(() => periods.filter((item) => item.available !== false), [periods])
  const availableKey = available.map((item) => item.period_id).join('|')

  useEffect(() => {
    setSelectedIds((current) => {
      const valid = current.filter((id) => available.some((item) => item.period_id === id))
      return valid.length >= 2 ? valid : available.map((item) => item.period_id)
    })
  }, [availableKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const mosaicFrames = useMemo(
    () => available.filter((item) => selectedIds.includes(item.period_id)),
    [available, selectedIds],
  )
  const sceneFrames = useMemo(
    () => (sceneResult?.scenes || [])
      .filter((scene) => scenePlaybackIds.includes(scene.scene_id))
      .map((scene) => ({ ...scene, period_id: scene.scene_id, source: 'cdse' })),
    [sceneResult, scenePlaybackIds],
  )
  const usingSceneFrames = studioMode === 'scenes' && sceneFrames.length > 0
  const frames = usingSceneFrames ? sceneFrames : mosaicFrames
  const frameIndex = Math.max(0, frames.findIndex((item) => item.period_id === playheadId))
  const current = frames[frameIndex] || mosaicFrames[0] || available[0]
  const displayLayer = activeLayer === 'satellite' ? 'rgb' : activeLayer
  const geometryKey = useMemo(() => JSON.stringify(zoneGeometry || null), [zoneGeometry])
  const selectedAoiBounds = useMemo(() => geometryBounds(zoneGeometry), [geometryKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const frameSetKey = frames.map((frame) => `${frame.period_id}:${frame.data_version || frame.acquired_at || ''}`).join('|')
  const centerKey = center.map((value) => Number(value).toFixed(5)).join('|')
  const preparationKey = `${usingSceneFrames ? 'cdse' : 'mosaic'}::${frameSetKey}::${displayLayer}::${geometryKey}::${centerKey}`
  const preloadReady = preload.status === 'ready' && preload.key === preparationKey

  function frameUrl(frame) {
    if (!frame) return ''
    return previewTileUrl(tileUrl(displayLayer, frame.period_id, frame.data_version || ''), center, 8)
  }

  function frameLabel(frame) {
    if (frame?.source === 'cdse') {
      return formatDate(new Date(frame.acquired_at), { dateStyle: 'medium' })
    }
    return periodLabel(frame)
  }

  function frameYear(frame) {
    return String(frame?.acquired_at || frame?.period_id || '').slice(0, 4)
  }

  function goToFrame(index, syncMap = false) {
    const next = frames[index]
    if (!next) return
    setPlayheadId(next.period_id)
    if (syncMap && next.source !== 'cdse' && next.period_id !== period) onPeriodChange(next.period_id)
  }

  function closeStudio() {
    setPlaying(false)
    setStudioOpen(false)
    if (current?.source !== 'cdse' && current?.period_id && current.period_id !== period) onPeriodChange(current.period_id)
  }

  function openStudio() {
    setPreloadRequested(true)
    setStudioOpen(true)
  }

  function togglePlayback() {
    if (!studioOpen || !preloadReady) {
      openStudio()
      return
    }
    setPlaying((value) => !value)
  }

  function step(direction) {
    setPlaying(false)
    const nextIndex = (frameIndex + direction + frames.length) % frames.length
    goToFrame(nextIndex)
  }

  function toggleFrame(periodId) {
    setPlaying(false)
    const isSelected = selectedIds.includes(periodId)
    if (isSelected && selectedIds.length <= 2) return
    const next = isSelected
      ? selectedIds.filter((id) => id !== periodId)
      : [...selectedIds, periodId]
    setSelectedIds(next)
    if (isSelected && periodId === current?.period_id) {
      const fallback = available.find((item) => next.includes(item.period_id))
      if (fallback) setPlayheadId(fallback.period_id)
    }
  }

  async function findScenes(event) {
    event.preventDefault()
    if (!selectedAoiBounds || !zoneGeometry) {
      setSceneError(t('timelapse.drawAoi'))
      return
    }
    setSceneLoading(true)
    setSceneError('')
    setSkippedSceneCount(0)
    try {
      const [south, west, north, east] = selectedAoiBounds.map(Number)
      const result = await searchTimelapseScenes({
        bbox: [west, south, east, north],
        geometry: zoneGeometry,
        startDate: sceneStartDate,
        endDate: sceneEndDate,
        maxCloudCover,
        limit: 30,
      })
      const scenes = uniqueSceneAcquisitions(result.scenes, 12)
      setSceneResult({ ...result, scenes, returned: scenes.length, catalogueReturned: result.returned })
      setSelectedSceneIds(scenes.map((scene) => scene.scene_id))
      setScenePlaybackIds([])
    } catch (error) {
      setSceneError(error.message || t('timelapse.sceneError'))
    } finally {
      setSceneLoading(false)
    }
  }

  function toggleScene(sceneId) {
    setPlaying(false)
    setSelectedSceneIds((currentIds) => currentIds.includes(sceneId)
      ? currentIds.filter((id) => id !== sceneId)
      : [...currentIds, sceneId])
  }

  function prepareSelectedScenes() {
    if (!selectedAoiBounds || !zoneGeometry) {
      setSceneError(t('timelapse.drawAoi'))
      return
    }
    const selectedScenes = (sceneResult?.scenes || []).filter((scene) => selectedSceneIds.includes(scene.scene_id))
    if (selectedScenes.length < 2) {
      setSceneError(t('timelapse.selectTwoScenes'))
      return
    }
    setSceneError('')
    setSkippedSceneCount(0)
    setPlaying(false)
    setScenePlaybackIds(selectedScenes.map((scene) => scene.scene_id))
    setPlayheadId(selectedScenes[0].scene_id)
    setPreloadRequested(true)
  }

  useEffect(() => {
    if (!preloadRequested || frames.length < 2) return undefined
    const controller = new AbortController()
    const preparedUrls = []
    let disposed = false
    let loaded = 0
    const sceneSource = frames.every((frame) => frame.source === 'cdse')
    let total = frames.length
    if (!sceneSource && selectedAoiBounds) {
      total = frames.length * tileGridForBounds(padBounds(selectedAoiBounds)).tiles.length
    }
    const preloadScope = sceneSource ? 'cdse' : (selectedAoiBounds ? 'aoi' : 'center')
    setPlaying(false)
    setPreload({ status: 'loading', loaded: 0, total, frames: {}, scope: preloadScope, error: '', key: preparationKey })

    const onAssetLoaded = () => {
      loaded += 1
      if (!disposed) setPreload((currentState) => ({ ...currentState, loaded }))
    }

    ;(async () => {
      const preparedFrames = {}
      if (sceneSource) {
        let cursor = 0
        async function worker() {
          while (cursor < frames.length) {
            const frame = frames[cursor]
            cursor += 1
            const response = await fetchTimelapseSceneFrame({
              geometry: zoneGeometry,
              sceneId: frame.scene_id,
              acquiredAt: frame.acquired_at,
              layer: displayLayer,
            }, { signal: controller.signal })
            if (response.coverage != null && response.coverage < MIN_SCENE_COVERAGE) {
              onAssetLoaded()
              continue
            }
            const prepared = await prepareSceneFrameBlob(response.blob, { onAssetLoaded })
            prepared.cached = response.cached
            prepared.coverage = response.coverage
            preparedUrls.push(prepared.url)
            preparedFrames[frame.period_id] = prepared
          }
        }
        await Promise.all(Array.from({ length: Math.min(2, frames.length) }, worker))
        const validFrameIds = frames
          .filter((frame) => preparedFrames[frame.period_id])
          .map((frame) => frame.period_id)
        const skipped = frames.length - validFrameIds.length
        if (skipped > 0) {
          if (validFrameIds.length < 2) {
            throw new Error(t('timelapse.notEnoughCoverage', { count: validFrameIds.length }))
          }
          setSkippedSceneCount((count) => count + skipped)
          setSelectedSceneIds((ids) => ids.filter((id) => validFrameIds.includes(id)))
          setPlayheadId(validFrameIds[0])
          setScenePlaybackIds(validFrameIds)
          return
        }
      } else {
        for (const frame of frames) {
          const template = tileUrl(displayLayer, frame.period_id, frame.data_version || '')
          const prepared = selectedAoiBounds
            ? await prepareAoiFrame(template, zoneGeometry, { signal: controller.signal, onAssetLoaded })
            : await prepareCenterFrame(template, center, { signal: controller.signal, onAssetLoaded })
          preparedUrls.push(prepared.url)
          preparedFrames[frame.period_id] = prepared
        }
      }
      if (!disposed) {
        setPreload({ status: 'ready', loaded: total, total, frames: preparedFrames, scope: preloadScope, error: '', key: preparationKey })
      }
    })().catch((error) => {
      if (disposed || error.name === 'AbortError') return
      preparedUrls.forEach((url) => URL.revokeObjectURL(url))
      preparedUrls.length = 0
      setPreload({
        status: 'error', loaded, total, frames: {}, scope: preloadScope,
        error: error.message || t('timelapse.preloadError'), key: preparationKey,
      })
    })

    return () => {
      disposed = true
      controller.abort()
      preparedUrls.forEach((url) => URL.revokeObjectURL(url))
    }
  }, [preloadRequested, preloadRetry, preparationKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open || frames.length < 2) setPlaying(false)
  }, [open, frames.length])

  useEffect(() => {
    if (!usingSceneFrames && frames.some((frame) => frame.period_id === period)) setPlayheadId(period)
  }, [period, frameSetKey, usingSceneFrames]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!playing || !preloadReady || frames.length < 2) return undefined
    const timer = window.setTimeout(() => {
      const currentIndex = frames.findIndex((item) => item.period_id === playheadId)
      const nextIndex = nextFrameIndex(currentIndex, frames.length, loop)
      if (nextIndex < 0) setPlaying(false)
      else goToFrame(nextIndex)
    }, 1000 / fps)
    return () => window.clearTimeout(timer)
  }, [playing, preloadReady, frames, playheadId, fps, loop]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!studioOpen) return undefined
    const handleKey = (event) => {
      if (event.key === 'Escape') closeStudio()
      if (event.key === 'ArrowLeft' && preloadReady) step(-1)
      if (event.key === 'ArrowRight' && preloadReady) step(1)
      if (event.key === ' ') {
        event.preventDefault()
        if (preloadReady) setPlaying((value) => !value)
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  })

  if (!open || available.length < 2 || !current) return null

  return (
    <>
      <section className="timelapse-control" aria-label={t('timelapse.aria')}>
        <button type="button" className={playing ? 'playing' : ''} onClick={togglePlayback} aria-pressed={playing}>
          <span aria-hidden="true">{playing ? 'Ⅱ' : '▶'}</span>
          {playing ? t('timelapse.pause') : t('timelapse.play')}
        </button>
        <label>
          <span>{frameLabel(current)}</span>
          <input
            type="range" min="0" max={frames.length - 1} step="1" value={frameIndex}
            onChange={(event) => { setPlaying(false); goToFrame(Number(event.target.value), !studioOpen) }}
            aria-label={t('timelapse.period')}
          />
        </label>
        <small>{selectedAoiBounds ? `${t('timelapse.aoiShort')} · ` : ''}{frameYear(frames[0])}–{frameYear(frames.at(-1))}</small>
        <button type="button" className="timelapse-expand" onClick={openStudio}>{t('timelapse.studio')}</button>
      </section>

      {studioOpen && (
        <div className="timelapse-studio-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeStudio() }}>
          <section className="timelapse-studio" role="dialog" aria-modal="true" aria-labelledby="timelapse-title">
            <header className="timelapse-studio-header">
              <div>
                <span className="panel-eyebrow">TIMELAPSE</span>
                <h2 id="timelapse-title">{t('timelapse.title')}</h2>
                <p>{usingSceneFrames ? t('timelapse.cdseSubtitle', { count: frames.length }) : (selectedAoiBounds ? t('timelapse.aoiSubtitle', { count: frames.length }) : t('timelapse.subtitle', { count: frames.length }))}</p>
              </div>
              <button type="button" onClick={closeStudio} aria-label={t('common.close')}>×</button>
            </header>

            <div className="timelapse-studio-grid">
              <aside className="timelapse-frame-panel">
                <div className="timelapse-period-summary">
                  <div><span>{t('timelapse.from')}</span><strong>{frameYear(frames[0])}</strong></div>
                  <div><span>{t('timelapse.to')}</span><strong>{frameYear(frames.at(-1))}</strong></div>
                </div>
                <p className="timelapse-data-note">{usingSceneFrames ? t('timelapse.cdseNote') : t('timelapse.annualNote')}</p>
                <div className="timelapse-source-tabs" role="tablist" aria-label={t('timelapse.sourceMode')}>
                  <button type="button" role="tab" aria-selected={studioMode === 'mosaics'} className={studioMode === 'mosaics' ? 'active' : ''} onClick={() => { setPlaying(false); setStudioMode('mosaics') }}>{t('timelapse.mosaics')}</button>
                  <button type="button" role="tab" aria-selected={studioMode === 'scenes'} className={studioMode === 'scenes' ? 'active' : ''} onClick={() => { setPlaying(false); setStudioMode('scenes') }}>{t('timelapse.sceneCatalog')}</button>
                </div>
                {studioMode === 'mosaics' ? (
                  <div className="timelapse-source-body">
                    <div className="timelapse-frame-heading">
                      <strong>{t('timelapse.frames')}</strong><span>{frames.length}/{available.length}</span>
                    </div>
                    <div className="timelapse-frame-list">
                      {available.map((frame) => {
                        const selected = selectedIds.includes(frame.period_id)
                        const active = frame.period_id === current.period_id
                        return (
                          <label key={frame.period_id} className={`timelapse-frame-card ${active ? 'active' : ''} ${selected ? '' : 'excluded'}`}>
                            <input type="checkbox" checked={selected} onChange={() => toggleFrame(frame.period_id)} />
                            <img src={frameUrl(frame)} alt="" loading="lazy" />
                            <span><strong>{periodLabel(frame)}</strong><small>{frame.date_range || frame.period_id}</small></span>
                          </label>
                        )
                      })}
                    </div>
                  </div>
                ) : (
                  <form className="timelapse-scene-search" onSubmit={findScenes}>
                    <div className="timelapse-date-fields">
                      <label><span>{t('timelapse.from')}</span><input type="date" value={sceneStartDate} max={sceneEndDate} onChange={(event) => setSceneStartDate(event.target.value)} required /></label>
                      <label><span>{t('timelapse.to')}</span><input type="date" value={sceneEndDate} min={sceneStartDate} onChange={(event) => setSceneEndDate(event.target.value)} required /></label>
                    </div>
                    <label className="timelapse-cloud-field">
                      <span>{t('timelapse.maxCloud')}</span><output>{maxCloudCover}%</output>
                      <input type="range" min="0" max="100" step="5" value={maxCloudCover} onChange={(event) => setMaxCloudCover(Number(event.target.value))} />
                    </label>
                    <button type="submit" disabled={sceneLoading}>{sceneLoading ? t('timelapse.searching') : t('timelapse.searchScenes')}</button>
                    {sceneError && <p className="timelapse-scene-error" role="alert">{sceneError}</p>}
                    {sceneResult && !sceneError && (
                      <>
                        <div className="timelapse-scene-summary">
                          <strong>{t('timelapse.sceneCount', { count: sceneResult.returned })}</strong>
                          {sceneResult.cached && <span>{t('timelapse.cached')}</span>}
                        </div>
                        <div className="timelapse-scene-list">
                          {sceneResult.scenes.map((scene) => (
                            <label key={scene.scene_id} className={`timelapse-scene-card ${selectedSceneIds.includes(scene.scene_id) ? '' : 'excluded'}`}>
                              <input type="checkbox" checked={selectedSceneIds.includes(scene.scene_id)} onChange={() => toggleScene(scene.scene_id)} />
                              <div><strong>{formatDate(new Date(scene.acquired_at), { dateStyle: 'medium', timeStyle: 'short' })}</strong><span>{scene.platform}</span></div>
                              <small>{scene.cloud_cover == null ? t('timelapse.cloudUnknown') : t('timelapse.cloudValue', { value: scene.cloud_cover })}</small>
                              <code title={scene.scene_id}>{scene.mgrs_tile || scene.scene_id}</code>
                              <em>{scene.renderable ? t('timelapse.renderable') : t('timelapse.catalogOnly')}</em>
                            </label>
                          ))}
                          {sceneResult.scenes.length === 0 && <p className="timelapse-scene-empty">{t('timelapse.noScenes')}</p>}
                        </div>
                        <button
                          type="button"
                          className="timelapse-prepare-scenes"
                          disabled={!selectedAoiBounds || selectedSceneIds.length < 2 || !sceneResult.capabilities?.scene_rendering}
                          onClick={prepareSelectedScenes}
                        >
                          {t('timelapse.prepareScenes', { count: selectedSceneIds.length })}
                        </button>
                        <p className="timelapse-catalog-note">{!selectedAoiBounds ? t('timelapse.drawAoi') : (sceneResult.capabilities?.scene_rendering ? t('timelapse.cdseCatalogNote') : t('timelapse.catalogNote'))}</p>
                      </>
                    )}
                  </form>
                )}
              </aside>

              <div className="timelapse-preview-panel">
                <div className={`timelapse-preview ${transition === 'fade' ? 'fade' : 'no-transition'} ${preload.scope !== 'center' ? 'aoi' : ''}`}>
                  {preloadReady && frames.map((frame) => (
                    <img
                      key={frame.period_id}
                      className={`timelapse-prepared-frame ${frame.period_id === current.period_id ? 'active' : ''}`}
                      src={preload.frames[frame.period_id]?.url}
                      alt={frame.period_id === current.period_id ? frameLabel(frame) : ''}
                    />
                  ))}
                  {!preloadReady && preload.status !== 'error' && (
                    <div className="timelapse-preload" role="status">
                      <span className="timelapse-preload-spinner" aria-hidden="true" />
                      <strong>{selectedAoiBounds ? t('timelapse.preparingAoi') : t('timelapse.preparing')}</strong>
                      <div><i style={{ width: `${preload.total ? Math.round(preload.loaded / preload.total * 100) : 0}%` }} /></div>
                      <small>{t('timelapse.preloadProgress', { loaded: preload.loaded, total: preload.total })}</small>
                    </div>
                  )}
                  {preload.status === 'error' && (
                    <div className="timelapse-preload error" role="alert">
                      <strong>{t('timelapse.preloadError')}</strong><small>{preload.error}</small>
                      <button type="button" onClick={() => setPreloadRetry((value) => value + 1)}>{t('timelapse.retry')}</button>
                    </div>
                  )}
                  <span className="timelapse-preview-layer">{selectedAoiBounds ? t('timelapse.aoiShort') : displayLayer.toUpperCase()}</span>
                  <strong>{frameLabel(current)}</strong>
                </div>
                {preloadReady && (
                  <div className="timelapse-ready-note">
                    <span aria-hidden="true">✓</span>{t('timelapse.ready', { count: frames.length })}
                    {skippedSceneCount > 0 && <small>{t('timelapse.skippedFrames', { count: skippedSceneCount })}</small>}
                  </div>
                )}
                <div className="timelapse-quality-note">
                    <span aria-hidden="true">!</span><p><strong>{t('timelapse.qualityTitle')}</strong>{usingSceneFrames ? t('timelapse.cdseQualityNote') : t('timelapse.qualityNote')}</p>
                </div>
              </div>
            </div>

            <footer className="timelapse-player">
              <div className="timelapse-step-actions">
                <button type="button" disabled={!preloadReady} onClick={() => step(-1)} aria-label={t('timelapse.previous')}>‹</button>
                <button type="button" disabled={!preloadReady} className="timelapse-main-play" onClick={togglePlayback} aria-pressed={playing}>{playing ? 'Ⅱ' : '▶'}</button>
                <button type="button" disabled={!preloadReady} onClick={() => step(1)} aria-label={t('timelapse.next')}>›</button>
              </div>
              <label>{t('timelapse.speed')}
                <select value={fps} onChange={(event) => setFps(Number(event.target.value))}>
                  {SPEED_OPTIONS.map((value) => <option key={value} value={value}>{value} fps</option>)}
                </select>
              </label>
              <label>{t('timelapse.transition')}
                <select value={transition} onChange={(event) => setTransition(event.target.value)}>
                  <option value="fade">{t('timelapse.fade')}</option><option value="none">{t('timelapse.none')}</option>
                </select>
              </label>
              <label className="timelapse-loop"><input type="checkbox" checked={loop} onChange={(event) => setLoop(event.target.checked)} /> {t('timelapse.loop')}</label>
              <input
                className="timelapse-studio-slider" type="range" min="0" max={frames.length - 1} value={frameIndex} disabled={!preloadReady}
                onChange={(event) => { setPlaying(false); goToFrame(Number(event.target.value)) }} aria-label={t('timelapse.period')}
              />
              <output>{frameIndex + 1}/{frames.length}</output>
            </footer>
          </section>
        </div>
      )}
    </>
  )
}
