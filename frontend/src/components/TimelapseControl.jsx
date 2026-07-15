import { useEffect, useMemo, useState } from 'react'
import { searchTimelapseScenes, tileUrl } from '../api.js'
import { useI18n } from '../i18n.jsx'
import { nextFrameIndex, previewTileUrl } from '../timelapse.js'

const SPEED_OPTIONS = [0.5, 1, 2, 4]

export default function TimelapseControl({
  open, periods = [], period, onPeriodChange, activeLayer = 'satellite', center = [43.3, 68.25],
  bounds = [40.31, 65.36, 46.46, 71.36],
}) {
  const { t, periodLabel, formatDate } = useI18n()
  const [playing, setPlaying] = useState(false)
  const [studioOpen, setStudioOpen] = useState(false)
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
  const [sceneLoading, setSceneLoading] = useState(false)
  const [sceneError, setSceneError] = useState('')
  const available = useMemo(() => periods.filter((item) => item.available !== false), [periods])
  const availableKey = available.map((item) => item.period_id).join('|')

  useEffect(() => {
    setSelectedIds((current) => {
      const valid = current.filter((id) => available.some((item) => item.period_id === id))
      return valid.length >= 2 ? valid : available.map((item) => item.period_id)
    })
  }, [availableKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const frames = useMemo(
    () => available.filter((item) => selectedIds.includes(item.period_id)),
    [available, selectedIds],
  )
  const frameIndex = Math.max(0, frames.findIndex((item) => item.period_id === period))
  const current = frames[frameIndex] || available[0]
  const displayLayer = activeLayer === 'satellite' ? 'rgb' : activeLayer

  function frameUrl(frame) {
    if (!frame) return ''
    return previewTileUrl(tileUrl(displayLayer, frame.period_id, frame.data_version || ''), center, 8)
  }

  function goToFrame(index) {
    const next = frames[index]
    if (next) onPeriodChange(next.period_id)
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
    if (isSelected && periodId === period) {
      const fallback = available.find((item) => next.includes(item.period_id))
      if (fallback) onPeriodChange(fallback.period_id)
    }
  }

  async function findScenes(event) {
    event.preventDefault()
    setSceneLoading(true)
    setSceneError('')
    try {
      const [south, west, north, east] = bounds.map(Number)
      const result = await searchTimelapseScenes({
        bbox: [west, south, east, north],
        startDate: sceneStartDate,
        endDate: sceneEndDate,
        maxCloudCover,
        limit: 30,
      })
      setSceneResult(result)
    } catch (error) {
      setSceneError(error.message || t('timelapse.sceneError'))
    } finally {
      setSceneLoading(false)
    }
  }

  useEffect(() => {
    if (!open || frames.length < 2) setPlaying(false)
  }, [open, frames.length])

  useEffect(() => {
    if (!playing || frames.length < 2) return undefined
    const timer = window.setTimeout(() => {
      const currentIndex = frames.findIndex((item) => item.period_id === period)
      const nextIndex = nextFrameIndex(currentIndex, frames.length, loop)
      if (nextIndex < 0) setPlaying(false)
      else goToFrame(nextIndex)
    }, 1000 / fps)
    return () => window.clearTimeout(timer)
  }, [playing, frames, period, fps, loop]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!studioOpen) return undefined
    const handleKey = (event) => {
      if (event.key === 'Escape') setStudioOpen(false)
      if (event.key === 'ArrowLeft') step(-1)
      if (event.key === 'ArrowRight') step(1)
      if (event.key === ' ') {
        event.preventDefault()
        setPlaying((value) => !value)
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  })

  if (!open || available.length < 2 || !current) return null

  return (
    <>
      <section className="timelapse-control" aria-label={t('timelapse.aria')}>
        <button type="button" className={playing ? 'playing' : ''} onClick={() => setPlaying((value) => !value)} aria-pressed={playing}>
          <span aria-hidden="true">{playing ? 'Ⅱ' : '▶'}</span>
          {playing ? t('timelapse.pause') : t('timelapse.play')}
        </button>
        <label>
          <span>{periodLabel(current)}</span>
          <input
            type="range" min="0" max={frames.length - 1} step="1" value={frameIndex}
            onChange={(event) => { setPlaying(false); goToFrame(Number(event.target.value)) }}
            aria-label={t('timelapse.period')}
          />
        </label>
        <small>{frames[0]?.period_id.slice(0, 4)}–{frames.at(-1)?.period_id.slice(0, 4)}</small>
        <button type="button" className="timelapse-expand" onClick={() => setStudioOpen(true)}>{t('timelapse.studio')}</button>
      </section>

      {studioOpen && (
        <div className="timelapse-studio-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setStudioOpen(false) }}>
          <section className="timelapse-studio" role="dialog" aria-modal="true" aria-labelledby="timelapse-title">
            <header className="timelapse-studio-header">
              <div>
                <span className="panel-eyebrow">TIMELAPSE</span>
                <h2 id="timelapse-title">{t('timelapse.title')}</h2>
                <p>{t('timelapse.subtitle', { count: frames.length })}</p>
              </div>
              <button type="button" onClick={() => setStudioOpen(false)} aria-label={t('common.close')}>×</button>
            </header>

            <div className="timelapse-studio-grid">
              <aside className="timelapse-frame-panel">
                <div className="timelapse-period-summary">
                  <div><span>{t('timelapse.from')}</span><strong>{available[0]?.period_id.slice(0, 4)}</strong></div>
                  <div><span>{t('timelapse.to')}</span><strong>{available.at(-1)?.period_id.slice(0, 4)}</strong></div>
                </div>
                <p className="timelapse-data-note">{t('timelapse.annualNote')}</p>
                <div className="timelapse-source-tabs" role="tablist" aria-label={t('timelapse.sourceMode')}>
                  <button type="button" role="tab" aria-selected={studioMode === 'mosaics'} className={studioMode === 'mosaics' ? 'active' : ''} onClick={() => setStudioMode('mosaics')}>{t('timelapse.mosaics')}</button>
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
                            <article key={scene.scene_id} className="timelapse-scene-card">
                              <div><strong>{formatDate(new Date(scene.acquired_at), { dateStyle: 'medium', timeStyle: 'short' })}</strong><span>{scene.platform}</span></div>
                              <small>{scene.cloud_cover == null ? t('timelapse.cloudUnknown') : t('timelapse.cloudValue', { value: scene.cloud_cover })}</small>
                              <code title={scene.scene_id}>{scene.mgrs_tile || scene.scene_id}</code>
                              <em>{t('timelapse.catalogOnly')}</em>
                            </article>
                          ))}
                          {sceneResult.scenes.length === 0 && <p className="timelapse-scene-empty">{t('timelapse.noScenes')}</p>}
                        </div>
                        <p className="timelapse-catalog-note">{t('timelapse.catalogNote')}</p>
                      </>
                    )}
                  </form>
                )}
              </aside>

              <div className="timelapse-preview-panel">
                <div className={`timelapse-preview ${transition === 'fade' ? 'fade' : ''}`} key={`${current.period_id}-${displayLayer}`}>
                  <img src={frameUrl(current)} alt={periodLabel(current)} />
                  <span className="timelapse-preview-layer">{displayLayer.toUpperCase()}</span>
                  <strong>{periodLabel(current)}</strong>
                </div>
                <div className="timelapse-quality-note">
                  <span aria-hidden="true">!</span><p><strong>{t('timelapse.qualityTitle')}</strong>{t('timelapse.qualityNote')}</p>
                </div>
              </div>
            </div>

            <footer className="timelapse-player">
              <div className="timelapse-step-actions">
                <button type="button" onClick={() => step(-1)} aria-label={t('timelapse.previous')}>‹</button>
                <button type="button" className="timelapse-main-play" onClick={() => setPlaying((value) => !value)} aria-pressed={playing}>{playing ? 'Ⅱ' : '▶'}</button>
                <button type="button" onClick={() => step(1)} aria-label={t('timelapse.next')}>›</button>
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
                className="timelapse-studio-slider" type="range" min="0" max={frames.length - 1} value={frameIndex}
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
