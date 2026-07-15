import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import ProductHeader from '../components/ProductHeader.jsx'
import {
  acknowledgeAlertEvent, fetchAlertEvents,
  fetchAccountZones, fetchCurrentAccount, fetchPeriods, fetchSavedAnalyses,
  fetchMonitoringStatus, fetchZoneStats, fetchZoneTimeSeries, importAccountZones,
  runAccountMonitoring,
} from '../api.js'
import { parseAoiFile } from '../aoiImport.js'
import { newZoneId, readSavedZones, writeSavedZones } from '../zoneStorage.js'
import { useI18n } from '../i18n.jsx'
import { copyText } from '../share.js'

function polygonAreaHa(geometry) {
  const ring = geometry?.coordinates?.[0]
  if (!Array.isArray(ring) || ring.length < 4) return null
  const radius = 6_378_137
  const radians = Math.PI / 180
  let area = 0
  for (let index = 0; index < ring.length - 1; index += 1) {
    const [lon1, lat1] = ring[index]
    const [lon2, lat2] = ring[index + 1]
    area += (lon2 - lon1) * radians * (2 + Math.sin(lat1 * radians) + Math.sin(lat2 * radians))
  }
  return Math.abs(area * radius * radius / 2) / 10_000
}

function parseCoordinateSearch(value) {
  const match = value.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)$/)
  if (!match) return null
  const lat = Number(match[1])
  const lon = Number(match[2])
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null
  return { lat, lon }
}

function ndviTone(value) {
  if (!Number.isFinite(value)) return 'unknown'
  if (value >= 0.45) return 'good'
  if (value >= 0.25) return 'moderate'
  return 'watch'
}

export default function DashboardPage() {
  const { t, formatDate } = useI18n()
  const navigate = useNavigate()
  const fileInputRef = useRef(null)
  const [account, setAccount] = useState(null)
  const [zones, setZones] = useState([])
  const [analyses, setAnalyses] = useState([])
  const [periods, setPeriods] = useState([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [importing, setImporting] = useState(false)
  const [snapshots, setSnapshots] = useState({})
  const [monitoringStatus, setMonitoringStatus] = useState(null)
  const [alerts, setAlerts] = useState([])
  const [monitoringBusy, setMonitoringBusy] = useState(false)

  const cloudMode = account?.user?.email_verified === true
  const latestPeriod = [...periods].reverse().find((item) => item.available !== false)?.period_id || '2025_summer'

  async function load() {
    setLoading(true)
    setError('')
    try {
      const [session, periodItems] = await Promise.all([fetchCurrentAccount(), fetchPeriods()])
      setAccount(session)
      setPeriods(periodItems || [])
      if (session?.user?.email_verified) {
        const [zoneResult, historyResult, statusResult, alertResult] = await Promise.all([
          fetchAccountZones(), fetchSavedAnalyses(), fetchMonitoringStatus(), fetchAlertEvents(),
        ])
        setZones(zoneResult.zones || [])
        setAnalyses(historyResult.analyses || [])
        setMonitoringStatus(statusResult)
        setAlerts(alertResult.alerts || [])
      } else {
        setZones(readSavedZones())
        setAnalyses([])
      }
    } catch (requestError) {
      setError(requestError.message || t('dashboard.loadError'))
      setZones(readSavedZones())
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [])

  const visibleZones = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    if (!needle || parseCoordinateSearch(query)) return zones
    return zones.filter((zone) => zone.name.toLocaleLowerCase().includes(needle))
  }, [query, zones])

  const alertCount = account?.preferences?.threshold_alerts?.length || 0
  const monitoredCount = Object.values(snapshots).filter((item) => item.status === 'ready').length

  function submitSearch(event) {
    event.preventDefault()
    const point = parseCoordinateSearch(query)
    if (!point) return
    const params = new URLSearchParams({
      lat: point.lat.toFixed(6), lon: point.lon.toFixed(6), period: latestPeriod, layer: 'ndvi',
    })
    navigate(`/map?${params}`)
  }

  async function importAois(event) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setImporting(true)
    setError('')
    setNotice('')
    try {
      const parsed = await parseAoiFile(file)
      if (parsed.length > 100) throw new Error(t('dashboard.importLimit'))
      const now = new Date().toISOString()
      const importedZones = parsed.map((item) => ({
        id: newZoneId(), name: item.name, geometry: item.geometry, createdAt: now, updatedAt: now,
      }))
      if (cloudMode) {
        const result = await importAccountZones(importedZones)
        setZones(result.zones || [])
        setNotice(t('dashboard.imported', { count: result.imported_count }))
      } else {
        const next = [...zones, ...importedZones]
        writeSavedZones(next)
        setZones(next)
        setNotice(t('dashboard.imported', { count: importedZones.length }))
      }
    } catch (requestError) {
      setError(requestError.message || t('dashboard.importError'))
    } finally {
      setImporting(false)
    }
  }

  async function refreshZone(zone) {
    setSnapshots((current) => ({ ...current, [zone.id]: { status: 'loading' } }))
    try {
      const [stats, timeSeries] = await Promise.all([
        fetchZoneStats(zone.geometry, latestPeriod),
        fetchZoneTimeSeries(zone.geometry),
      ])
      const observations = timeSeries.observations || []
      const latest = observations.at(-1)?.indices?.ndvi?.mean
      const previous = observations.at(-2)?.indices?.ndvi?.mean
      setSnapshots((current) => ({
        ...current,
        [zone.id]: {
          status: 'ready', stats, timeSeries,
          ndvi: Number.isFinite(latest) ? latest : stats.indices?.ndvi?.mean,
          change: Number.isFinite(latest) && Number.isFinite(previous) ? latest - previous : null,
          checkedAt: new Date().toISOString(),
        },
      }))
    } catch (requestError) {
      setSnapshots((current) => ({
        ...current, [zone.id]: { status: 'error', error: requestError.message || t('dashboard.snapshotError') },
      }))
    }
  }

  async function runMonitoring() {
    setMonitoringBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await runAccountMonitoring()
      setNotice(t('dashboard.monitoringComplete', {
        zones: result.zones_checked,
        alerts: result.alerts_created,
      }))
      const [statusResult, alertResult] = await Promise.all([fetchMonitoringStatus(), fetchAlertEvents()])
      setMonitoringStatus(statusResult)
      setAlerts(alertResult.alerts || [])
    } catch (requestError) {
      setError(requestError.message || t('dashboard.monitoringError'))
    } finally {
      setMonitoringBusy(false)
    }
  }

  async function acknowledge(alertId) {
    try {
      const updated = await acknowledgeAlertEvent(alertId)
      setAlerts((current) => current.map((item) => item.id === alertId ? { ...item, ...updated } : item))
    } catch (requestError) {
      setError(requestError.message || t('dashboard.acknowledgeError'))
    }
  }

  async function shareZone(zone) {
    const url = new URL('/map', window.location.origin)
    url.searchParams.set('zone', zone.id)
    url.searchParams.set('period', latestPeriod)
    url.searchParams.set('layer', 'ndvi')
    await copyText(url.toString())
    setNotice(t('dashboard.linkCopied'))
  }

  function displayedDate(value) {
    if (!value) return t('dashboard.notAvailable')
    const date = new Date(value)
    return Number.isNaN(date.getTime()) ? value : formatDate(date, { dateStyle: 'medium' })
  }

  return (
    <div className="page-shell">
      <ProductHeader account={account} />
      <main className="product-page dashboard-page">
        <section className="page-hero">
          <div>
            <span className="page-eyebrow">{t('dashboard.eyebrow')}</span>
            <h1>{t('dashboard.title')}</h1>
            <p>{t('dashboard.subtitle')}</p>
          </div>
          <div className="page-hero-actions">
            {cloudMode && <button type="button" className="secondary-action" onClick={runMonitoring} disabled={monitoringBusy}>{monitoringBusy ? t('common.wait') : t('dashboard.runMonitoring')}</button>}
            <button type="button" className="secondary-action" onClick={() => fileInputRef.current?.click()} disabled={importing}>
              {importing ? t('common.wait') : t('dashboard.import')}
            </button>
            <input
              ref={fileInputRef}
              className="sr-only"
              type="file"
              accept=".geojson,.json,.kml,.wkt,.txt,application/geo+json,application/json"
              onChange={importAois}
            />
            <Link className="primary-action" to="/map">{t('dashboard.openMap')}</Link>
          </div>
        </section>

        <section className="metric-grid" aria-label={t('dashboard.summary')}>
          <article><span>{t('dashboard.savedZones')}</span><strong>{zones.length}</strong><small>{cloudMode ? t('common.cloud') : t('dashboard.local')}</small></article>
          <article><span>{t('dashboard.checked')}</span><strong>{monitoredCount}</strong><small>{t('dashboard.thisSession')}</small></article>
          <article><span>{t('dashboard.alertRules')}</span><strong>{monitoringStatus?.active_alerts ?? 0}</strong><small>{t('dashboard.activeOfRules', { count: alertCount })}</small></article>
          <article><span>{t('dashboard.analyses')}</span><strong>{analyses.length}</strong><small><Link to="/history">{t('dashboard.openHistory')}</Link></small></article>
        </section>

        <section className="dashboard-toolbar">
          <form onSubmit={submitSearch}>
            <label htmlFor="field-search">{t('dashboard.searchLabel')}</label>
            <div>
              <input
                id="field-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t('dashboard.searchPlaceholder')}
              />
              <button type="submit" disabled={!parseCoordinateSearch(query)}>{t('dashboard.goToPoint')}</button>
            </div>
            <small>{t('dashboard.searchHelp')}</small>
          </form>
          <button type="button" className="refresh-action" onClick={load} disabled={loading}>{t('history.refresh')}</button>
        </section>

        {(error || notice) && <div className={`page-message ${error ? 'error' : 'success'}`} role={error ? 'alert' : 'status'}>{error || notice}</div>}

        {cloudMode && alerts.length > 0 && (
          <section className="alert-feed" aria-labelledby="alert-feed-title">
            <div className="alert-feed-heading">
              <div><span className="page-eyebrow">MONITORING</span><h2 id="alert-feed-title">{t('dashboard.alertFeed')}</h2></div>
              <small>{monitoringStatus?.service?.enabled ? t('dashboard.schedulerOn') : t('dashboard.schedulerManual')}</small>
            </div>
            <div className="alert-feed-list">
              {alerts.slice(0, 8).map((alert) => (
                <article className={`alert-event ${alert.status}`} key={alert.id}>
                  <span className="alert-event-icon" aria-hidden="true">{alert.status === 'resolved' ? '✓' : '!'}</span>
                  <div>
                    <strong>{alert.zone_name}</strong>
                    <p>{alert.index.toUpperCase()} {alert.operator === 'below' ? '<' : '>'} {Number(alert.threshold).toFixed(2)} · {t('dashboard.observed')} {Number(alert.observed_value).toFixed(3)}</p>
                    <small>{displayedDate(alert.last_observed_at)} · {t(`dashboard.alertStatus.${alert.status}`)}</small>
                  </div>
                  {alert.status === 'open' && <button type="button" onClick={() => acknowledge(alert.id)}>{t('dashboard.acknowledge')}</button>}
                  <Link to={`/map?zone=${encodeURIComponent(alert.zone_id)}&period=${encodeURIComponent(alert.period_id)}&layer=${encodeURIComponent(alert.index)}`}>{t('dashboard.open')}</Link>
                </article>
              ))}
            </div>
          </section>
        )}

        {loading ? (
          <div className="page-empty">{t('common.wait')}</div>
        ) : visibleZones.length === 0 ? (
          <section className="page-empty">
            <h2>{query ? t('dashboard.noSearchResults') : t('dashboard.emptyTitle')}</h2>
            <p>{query ? t('dashboard.noSearchHelp') : t('dashboard.emptyHelp')}</p>
          </section>
        ) : (
          <section className="zone-card-grid" aria-label={t('dashboard.savedZones')}>
            {visibleZones.map((zone) => {
              const snapshot = snapshots[zone.id]
              const tone = ndviTone(snapshot?.ndvi)
              const area = snapshot?.stats?.area_ha ?? polygonAreaHa(zone.geometry)
              const params = new URLSearchParams({ zone: zone.id, period: latestPeriod, layer: 'ndvi' })
              return (
                <article className="zone-card" key={zone.id}>
                  <div className="zone-card-heading">
                    <span className={`condition-dot ${tone}`} aria-hidden="true" />
                    <div><h2>{zone.name}</h2><small>{displayedDate(zone.updatedAt || zone.createdAt)}</small></div>
                    <span className="storage-badge">{cloudMode ? t('common.cloud') : t('dashboard.local')}</span>
                  </div>
                  <div className="zone-card-metrics">
                    <div><span>{t('dashboard.area')}</span><strong>{Number.isFinite(area) ? `${area.toFixed(area >= 100 ? 0 : 1)} ${t('unit.ha')}` : '—'}</strong></div>
                    <div><span>NDVI</span><strong>{Number.isFinite(snapshot?.ndvi) ? snapshot.ndvi.toFixed(3) : '—'}</strong></div>
                    <div><span>{t('dashboard.change')}</span><strong className={snapshot?.change > 0 ? 'positive' : snapshot?.change < 0 ? 'negative' : ''}>{Number.isFinite(snapshot?.change) ? `${snapshot.change > 0 ? '+' : ''}${snapshot.change.toFixed(3)}` : '—'}</strong></div>
                  </div>
                  {snapshot?.status === 'error' && <p className="zone-card-error" role="alert">{snapshot.error}</p>}
                  {snapshot?.checkedAt && <p className="zone-card-checked">{t('dashboard.checkedAt', { date: displayedDate(snapshot.checkedAt) })}</p>}
                  <div className="zone-card-actions">
                    <button type="button" onClick={() => refreshZone(zone)} disabled={snapshot?.status === 'loading'}>{snapshot?.status === 'loading' ? t('common.wait') : t('dashboard.checkNow')}</button>
                    <Link to={`/map?${params}`}>{t('dashboard.analyze')}</Link>
                    <button type="button" onClick={() => shareZone(zone)}>{t('dashboard.share')}</button>
                  </div>
                </article>
              )
            })}
          </section>
        )}
      </main>
    </div>
  )
}
