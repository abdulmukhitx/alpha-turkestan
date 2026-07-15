import { useEffect, useMemo, useState } from 'react'
import {
  ResponsiveContainer, ComposedChart, Area, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, ReferenceLine,
} from 'recharts'
import { useI18n } from '../i18n.jsx'

const ALERT_STORAGE_KEY = 'geoai-tko.threshold-alerts.v1'
const INDEX_OPTIONS = [
  ['ndvi', 'NDVI'], ['ndre', 'NDRE'], ['ndwi', 'NDWI'], ['ndmi', 'NDMI'],
  ['bsi', 'BSI'], ['savi', 'SAVI'], ['nbr', 'NBR'],
]
const DEFAULT_THRESHOLDS = { ndvi: 0.2, ndre: 0.15, ndwi: 0, ndmi: -0.1, bsi: 0.2, savi: 0.15, nbr: -0.15 }

function readAlerts() {
  try {
    return normalizeAlerts(JSON.parse(window.localStorage.getItem(ALERT_STORAGE_KEY) || '[]'))
  } catch {
    return []
  }
}

function normalizeAlerts(value) {
  return Array.isArray(value)
    ? value.filter((rule) => INDEX_OPTIONS.some(([key]) => key === rule.index)
      && ['below', 'above'].includes(rule.operator) && Number.isFinite(Number(rule.value)))
      .map((rule) => ({ ...rule, value: Number(rule.value) }))
    : []
}

function ruleId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID()
  return `alert-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function ruleMatches(rule, value) {
  if (!Number.isFinite(value)) return false
  return rule.operator === 'below' ? value < rule.value : value > rule.value
}

function codeFor(index) {
  return INDEX_OPTIONS.find(([key]) => key === index)?.[1] || index.toUpperCase()
}

function ChartTooltip({ active, payload, label, mode }) {
  const { t } = useI18n()
  if (!active || !payload?.length) return null
  const mean = payload.find((entry) => entry.dataKey === 'chartValue')?.value
  const range = payload.find((entry) => entry.dataKey === 'range')?.value
  return (
    <div className="timeseries-tooltip">
      <strong>{label}</strong>
      {Number.isFinite(mean) && <span>{mode === 'anomaly' ? t('timeseries.anomaly') : t('timeseries.mean')}: {mean > 0 && mode === 'anomaly' ? '+' : ''}{mean.toFixed(4)}</span>}
      {mode !== 'anomaly' && Array.isArray(range) && <span>p10–p90: {range[0].toFixed(4)} … {range[1].toFixed(4)}</span>}
    </div>
  )
}

export default function ZoneTimeSeries({
  data, loading, error, activeLayer, alertRules, onAlertRulesChange, alertsCloudMode = false,
}) {
  const { t, formatNumber, periodLabel } = useI18n()
  const initialIndex = INDEX_OPTIONS.some(([key]) => key === activeLayer) ? activeLayer : 'ndvi'
  const [selectedIndex, setSelectedIndex] = useState(initialIndex)
  const [operator, setOperator] = useState('below')
  const [threshold, setThreshold] = useState(DEFAULT_THRESHOLDS[initialIndex])
  const [chartMode, setChartMode] = useState('value')
  const [alerts, setAlerts] = useState(() => alertsCloudMode ? normalizeAlerts(alertRules) : readAlerts())
  const [storageError, setStorageError] = useState(null)

  useEffect(() => {
    if (!INDEX_OPTIONS.some(([key]) => key === activeLayer)) return
    setSelectedIndex(activeLayer)
    setThreshold(DEFAULT_THRESHOLDS[activeLayer])
  }, [activeLayer])

  useEffect(() => {
    if (alertsCloudMode) setAlerts(normalizeAlerts(alertRules))
  }, [alertRules, alertsCloudMode])

  const observations = data?.observations || []
  const baseline = data?.baselines?.[selectedIndex]
  const chartData = useMemo(() => observations.map((item) => {
    const stats = item.indices?.[selectedIndex]
    const anomaly = item.anomalies?.[selectedIndex]
    return {
      year: item.year,
      mean: stats?.mean ?? null,
      anomaly: anomaly ?? null,
      chartValue: chartMode === 'anomaly' ? (anomaly ?? null) : (stats?.mean ?? null),
      range: stats ? [stats.p10, stats.p90] : null,
    }
  }), [observations, selectedIndex, chartMode])

  const alertSummaries = useMemo(() => alerts.map((rule) => {
    const breaches = observations.filter((item) => ruleMatches(rule, item.indices?.[rule.index]?.mean))
    return { ...rule, breaches }
  }), [alerts, observations])
  const latest = observations[observations.length - 1]
  const activeBreaches = alertSummaries.filter((rule) => ruleMatches(rule, latest?.indices?.[rule.index]?.mean))

  async function persist(next) {
    const previous = alerts
    setAlerts(next)
    if (alertsCloudMode) {
      try {
        await onAlertRulesChange(next)
        setStorageError(null)
      } catch (requestError) {
        setAlerts(previous)
        setStorageError(requestError.message || t('timeseries.cloudStorageError'))
      }
      return
    }
    try {
      window.localStorage.setItem(ALERT_STORAGE_KEY, JSON.stringify(next))
      setStorageError(null)
    } catch {
      setStorageError(t('timeseries.storageError'))
    }
  }

  async function addAlert(event) {
    event.preventDefault()
    const value = Number(threshold)
    if (!Number.isFinite(value) || value < -1 || value > 1) return
    await persist([...alerts, { id: ruleId(), index: selectedIndex, operator, value }])
  }

  if (!loading && !error && !data) return null

  return (
    <div className="zone-block zone-timeseries">
      <div className="zone-block-title zone-timeseries-title">
        <span>{t('timeseries.series')}</span>
        {observations.length > 0 && <span>{observations[0].year}–{observations[observations.length - 1].year}</span>}
      </div>
      {alertsCloudMode && <div className="timeseries-cloud-note">{t('timeseries.cloudRules')}</div>}

      {loading && <div className="zone-loading" role="status">{t('timeseries.collecting')}</div>}
      {!loading && error && <div className="zone-error">{error}</div>}

      {!loading && !error && observations.length > 0 && (
        <>
          <label className="timeseries-index-picker">
            <span>{t('timeseries.indexPicker')}</span>
            <select value={selectedIndex} onChange={(event) => {
              const next = event.target.value
              setSelectedIndex(next)
              setThreshold(DEFAULT_THRESHOLDS[next])
            }}>
              {INDEX_OPTIONS.map(([key, label]) => <option value={key} key={key}>{label}</option>)}
            </select>
          </label>

          <div className="timeseries-mode" role="group" aria-label={t('timeseries.chartMode')}>
            <button type="button" className={chartMode === 'value' ? 'active' : ''} onClick={() => setChartMode('value')}>{t('timeseries.values')}</button>
            <button type="button" className={chartMode === 'anomaly' ? 'active' : ''} onClick={() => setChartMode('anomaly')}>{t('timeseries.anomalies')}</button>
          </div>

          <div className="timeseries-chart" aria-label={t('forecast.seriesAria', { code: codeFor(selectedIndex) })}>
            <ResponsiveContainer width="100%" height={210}>
              <ComposedChart data={chartData} margin={{ top: 14, right: 8, bottom: 0, left: -18 }}>
                <defs>
                  <linearGradient id="zoneRange" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#3B82F6" stopOpacity={0.28} />
                    <stop offset="100%" stopColor="#3B82F6" stopOpacity={0.04} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 4" vertical={false} />
                <XAxis dataKey="year" tick={{ fill: 'var(--text3)', fontSize: 10 }} axisLine={{ stroke: 'var(--border)' }} tickLine={false} />
                <YAxis domain={chartMode === 'anomaly' ? ['auto', 'auto'] : [-1, 1]} allowDataOverflow tick={{ fill: 'var(--text3)', fontSize: 9 }} axisLine={false} tickLine={false} tickFormatter={(value) => value.toFixed(1)} />
                <Tooltip content={<ChartTooltip mode={chartMode} />} />
                {chartMode === 'value' && <Area type="monotone" dataKey="range" name="p10–p90" stroke="none" fill="url(#zoneRange)" isAnimationActive={false} />}
                <ReferenceLine y={chartMode === 'anomaly' ? 0 : baseline?.median} stroke="#A78BFA" strokeDasharray="3 4" />
                <Line type="monotone" dataKey="chartValue" name={chartMode === 'anomaly' ? t('timeseries.anomaly') : t('timeseries.mean')} stroke={chartMode === 'anomaly' ? '#A78BFA' : '#60A5FA'} strokeWidth={2.5} dot={{ r: 3, fill: '#0F172A', stroke: chartMode === 'anomaly' ? '#A78BFA' : '#60A5FA', strokeWidth: 2 }} connectNulls isAnimationActive={false} />
                {alerts.filter((rule) => rule.index === selectedIndex).map((rule) => (
                  <ReferenceLine
                    key={rule.id}
                    y={rule.value}
                    stroke="#F97316"
                    strokeDasharray="5 4"
                    ifOverflow="extendDomain"
                  />
                ))}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p className="sr-only">
            {t('timeseries.chartSummary', {
              index: codeFor(selectedIndex),
              count: observations.length,
              year: latest?.year || '',
              value: Number.isFinite(latest?.indices?.[selectedIndex]?.mean)
                ? formatNumber(latest.indices[selectedIndex].mean, { maximumFractionDigits: 4 })
                : t('account.notAvailable'),
            })}
          </p>

          <div className={`threshold-status ${activeBreaches.length ? 'alerting' : ''}`} role="status">
            <strong>{activeBreaches.length ? t('timeseries.activeWarnings', { count: activeBreaches.length }) : t('timeseries.thresholdsNormal')}</strong>
            <span>{latest ? t('timeseries.latestPeriod', { label: periodLabel(latest.label || latest.period_id || latest.year) }) : ''}</span>
          </div>

          <form className="threshold-form" onSubmit={addAlert}>
            <div className="zone-block-title">{t('timeseries.newRule')}</div>
            <div className="threshold-fields">
              <select value={selectedIndex} onChange={(event) => {
                const next = event.target.value
                setSelectedIndex(next)
                setThreshold(DEFAULT_THRESHOLDS[next])
              }} aria-label={t('timeseries.ruleIndex')}>
                {INDEX_OPTIONS.map(([key, label]) => <option value={key} key={key}>{label}</option>)}
              </select>
              <select value={operator} onChange={(event) => setOperator(event.target.value)} aria-label={t('timeseries.ruleCondition')}>
                <option value="below">{t('timeseries.below')}</option>
                <option value="above">{t('timeseries.above')}</option>
              </select>
              <input
                type="number" min="-1" max="1" step="0.01" value={threshold}
                onChange={(event) => setThreshold(event.target.value)} aria-label={t('timeseries.threshold')}
              />
              <button type="submit">{t('timeseries.add')}</button>
            </div>
          </form>

          {alertSummaries.length > 0 && (
            <div className="threshold-rule-list">
              {alertSummaries.map((rule) => (
                <div className={ruleMatches(rule, latest?.indices?.[rule.index]?.mean) ? 'breached' : ''} key={rule.id}>
                  <span>
                    <strong>{codeFor(rule.index)}</strong> {rule.operator === 'below' ? '<' : '>'} {rule.value.toFixed(2)}
                    <small> · {rule.breaches.length ? t('timeseries.periodsOutside', { count: rule.breaches.length }) : t('timeseries.noBreaches')}</small>
                  </span>
                  <button type="button" onClick={() => persist(alerts.filter((item) => item.id !== rule.id))} aria-label={t('timeseries.deleteRule')}>×</button>
                </div>
              ))}
            </div>
          )}
          {storageError && <div className="zone-error">{storageError}</div>}
        </>
      )}
    </div>
  )
}
