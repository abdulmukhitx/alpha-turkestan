import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip } from 'recharts'
import { useI18n } from '../i18n.jsx'
import SaveAnalysisAction from './SaveAnalysisAction.jsx'
import EvidenceBadge from './EvidenceBadge.jsx'

const INDEX_CODES = {
  ndvi: 'NDVI', ndwi: 'NDWI', ndre: 'NDRE', ndmi: 'NDMI',
  bsi: 'BSI', savi: 'SAVI', nbr: 'NBR',
}

const DIRECTION_COLORS = {
  improving: '#22c55e',
  degrading: '#ef4444',
  stable: '#eab308',
}

export default function ForecastPanel({ point, result, loading, error, targetYear, index, canSaveAnalysis, onSaveAnalysis, onClose }) {
  const { t } = useI18n()
  const trend = result?.trend
  const code = INDEX_CODES[result?.index || index] || (result?.index || index || '').toUpperCase()
  const color = DIRECTION_COLORS[trend?.direction] || 'var(--text2)'
  const history = result?.history || []
  const chartData = trend ? [
    ...history.map((item, itemIndex) => ({
      year: item.year,
      observed: item.value,
      forecast: itemIndex === history.length - 1 ? item.value : null,
    })),
    { year: result.target_year, observed: null, forecast: trend.predicted },
  ] : []

  return (
    <aside className="panel panel-right" id="analysis-panel" aria-label={t('forecast.results')}>
      <div className="panel-header panel-header-row">
        <div>
          <span className="panel-eyebrow">{t('forecast.eyebrow')}</span>
          <h2 className="panel-title">{t('forecast.linearTrend', { year: targetYear })}</h2>
        </div>
        <button className="panel-close" type="button" onClick={onClose} aria-label={t('analysis.hide')}>×</button>
      </div>
      <SaveAnalysisAction visible={canSaveAnalysis && !!result} onSave={onSaveAnalysis} />

      {!point ? (
        <div className="click-prompt">
          <svg className="click-icon" width="30" height="30" viewBox="0 0 32 32" fill="none">
            <path d="M5 24 12 17l5 4 10-13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            <path d="m21 8 6 0 0 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <p className="click-text">{t('forecast.clickHelp')}</p>
        </div>
      ) : (
        <>
          <div className="result-location">
            <span>{point.lat.toFixed(4)}°N · {point.lng.toFixed(4)}°E · {code}</span>
          </div>

          <EvidenceBadge evidence={result?.evidence} />

          {loading && <div className="forecast-loading" role="status">{t('forecast.calculating')}</div>}
          {error && <div className="zone-error">{error}</div>}

          {trend && !loading && (
            <>
              <div className="forecast-summary-grid">
                <div className="forecast-summary-card">
                  <span>{t('forecast.latest')}</span>
                  <strong>{trend.latest_value.toFixed(4)}</strong>
                </div>
                <div className="forecast-summary-card forecast-summary-primary">
                  <span>{t('map.forecast', { year: result.target_year })}</span>
                  <strong>{trend.predicted.toFixed(4)}</strong>
                </div>
              </div>

              <div className="forecast-direction" style={{ borderColor: color }}>
                <span style={{ color }}>{t(`forecast.direction.${trend.direction}`)}</span>
                <strong style={{ color }}>
                  {trend.change_from_latest >= 0 ? '+' : ''}{trend.change_from_latest.toFixed(4)}
                </strong>
              </div>

              <div className="section-label">{t('forecast.observedHistory')}</div>
              <div className="forecast-timeseries" aria-label={t('forecast.seriesAria', { code })}>
                <ResponsiveContainer width="100%" height={190}>
                  <LineChart data={chartData} margin={{ top: 10, right: 8, bottom: 0, left: -18 }}>
                    <CartesianGrid stroke="var(--border)" strokeDasharray="3 4" vertical={false} />
                    <XAxis dataKey="year" tick={{ fill: 'var(--text3)', fontSize: 10 }} axisLine={{ stroke: 'var(--border)' }} tickLine={false} />
                    <YAxis domain={[-1, 1]} allowDataOverflow tick={{ fill: 'var(--text3)', fontSize: 9 }} axisLine={false} tickLine={false} tickFormatter={(value) => value.toFixed(1)} />
                    <Tooltip formatter={(value, name) => [Number(value).toFixed(4), name === 'observed' ? t('forecast.observation') : t('forecast.title')]} />
                    <Line type="monotone" dataKey="observed" stroke="#60A5FA" strokeWidth={2.5} dot={{ r: 3 }} connectNulls={false} isAnimationActive={false} />
                    <Line type="monotone" dataKey="forecast" stroke="#34D399" strokeWidth={2.5} strokeDasharray="6 4" dot={{ r: 3 }} connectNulls isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              <div className="forecast-metrics">
                <div><span>{t('forecast.trend')}</span><strong>{trend.slope_per_year >= 0 ? '+' : ''}{trend.slope_per_year.toFixed(4)}</strong></div>
                <div><span>{t('forecast.sensitivity')}</span><strong>{trend.sensitivity_low.toFixed(4)} … {trend.sensitivity_high.toFixed(4)}</strong></div>
                <div><span>{t('forecast.lineQuality')}</span><strong>{trend.trend_quality === 'consistent' ? t('forecast.consistent') : t('forecast.variable')}</strong></div>
                <div><span>R² · {t('forecast.history')}</span><strong>{trend.r_squared.toFixed(3)}</strong></div>
              </div>

              <div className="forecast-disclaimer">{t('forecast.experimental')}</div>
            </>
          )}
        </>
      )}
    </aside>
  )
}
