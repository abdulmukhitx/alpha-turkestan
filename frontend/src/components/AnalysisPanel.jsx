import ZoneStatsPanel from './ZoneStatsPanel.jsx'
import TransectChart from './TransectChart.jsx'
import ChangeStatsPanel from './ChangeStatsPanel.jsx'
import ForecastPanel from './ForecastPanel.jsx'

function PanelHeader({ hasPoint, onClose }) {
  return (
    <div className="panel-header panel-header-row">
      <div>
        <span className="panel-eyebrow">{hasPoint ? 'Выбранная точка' : 'Данные и выводы'}</span>
        <h2 className="panel-title">Результаты анализа</h2>
      </div>
      <button className="panel-close" type="button" onClick={onClose} aria-label="Скрыть панель результатов">×</button>
    </div>
  )
}

// order + Russian labels for the spectral indices panel
const INDICES = [
  { key: 'ndvi', code: 'NDVI', label: 'Растительность' },
  { key: 'ndre', code: 'NDRE', label: 'Стресс растений' },
  { key: 'ndwi', code: 'NDWI', label: 'Водные ресурсы' },
  { key: 'ndmi', code: 'NDMI', label: 'Влажность почвы' },
  { key: 'bsi',  code: 'BSI',  label: 'Голая почва' },
  { key: 'savi', code: 'SAVI', label: 'Покрытие раст.' },
  { key: 'nbr',  code: 'NBR',  label: 'Деградация' },
]

// semantic colour per index value (green=good veg, blue=water, red/orange=dry/bare)
function indexColor(key, v) {
  if (v == null) return 'var(--text3)'
  switch (key) {
    case 'ndvi':
      if (v > 0.4) return '#16a34a'
      if (v > 0.2) return '#84cc16'
      if (v > 0.1) return '#eab308'
      return '#ef4444'
    case 'ndre':
      if (v > 0.4) return '#16a34a'
      if (v > 0.2) return '#84cc16'
      if (v > 0.05) return '#eab308'
      return '#ef4444'
    case 'ndwi':
      if (v > 0.2) return '#2563eb'
      if (v > 0) return '#60a5fa'
      if (v > -0.2) return '#f59e0b'
      return '#ef4444'
    case 'ndmi':
      if (v > 0.1) return '#2563eb'
      if (v > -0.1) return '#f59e0b'
      return '#ef4444'
    case 'bsi':
      if (v > 0.2) return '#dc2626'
      if (v > 0.1) return '#f97316'
      if (v > 0) return '#fbbf24'
      return '#22c55e'
    case 'savi':
      if (v > 0.15) return '#16a34a'
      if (v >= 0) return '#eab308'
      return '#ef4444'
    case 'nbr':
      if (v > 0) return '#16a34a'
      if (v >= -0.15) return '#eab308'
      return '#ef4444'
    default:
      return 'var(--text3)'
  }
}

export default function AnalysisPanel({
  point, pixel, aiText, loading, error,
  zoneStats, zoneLoading, zoneError, zoneGeometry, activeLayer, zonePeriod,
  transectData, transectLoading, transectError,
  changeStats, changeLoading, changeError,
  forecastMode, forecastResult, forecastLoading, forecastError, forecastYear, forecastIndex,
  onClose,
}) {
  if (forecastMode) {
    return (
      <ForecastPanel
        point={point}
        result={forecastResult}
        loading={forecastLoading}
        error={forecastError}
        targetYear={forecastYear}
        index={forecastIndex}
        onClose={onClose}
      />
    )
  }

  if (!point) {
    return (
      <aside className="panel panel-right" id="analysis-panel" aria-label="Результаты анализа">
        <PanelHeader hasPoint={false} onClose={onClose} />
        <div className="click-prompt">
          <svg className="click-icon" width="30" height="30" viewBox="0 0 32 32" fill="none">
            <circle cx="16" cy="16" r="14" stroke="currentColor" strokeWidth="1" strokeDasharray="3 2" opacity="0.5" />
            <circle cx="16" cy="16" r="6" stroke="currentColor" strokeWidth="1.5" />
            <circle cx="16" cy="16" r="2" fill="currentColor" />
          </svg>
          <strong>Выберите объект на карте</strong>
          <p className="click-text">Кликните по точке или используйте инструменты зоны и профиля. Результаты появятся здесь.</p>
        </div>
        <ZoneStatsPanel
          stats={zoneStats} loading={zoneLoading} error={zoneError}
          geometry={zoneGeometry} activeLayer={activeLayer} period={zonePeriod}
        />
        <TransectChart data={transectData} loading={transectLoading} error={transectError} />
        <ChangeStatsPanel stats={changeStats} loading={changeLoading} error={changeError} />
      </aside>
    )
  }

  return (
    <aside className="panel panel-right" id="analysis-panel" aria-label="Результаты анализа">
      <PanelHeader hasPoint onClose={onClose} />

      <div className="result-location">
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
          <circle cx="6" cy="5" r="3" stroke="currentColor" strokeWidth="1.2" />
          <path d="M6 9C6 9 2 6.5 2 5a4 4 0 0 1 8 0C10 6.5 6 9 6 9z" stroke="currentColor" strokeWidth="1.2" />
        </svg>
        <span>{point.lat.toFixed(4)}°N · {point.lng.toFixed(4)}°E</span>
      </div>

      {pixel?.ml_class_ru && (
        <div className="ml-block">
          <div className="ml-header">
            <span>ML-классификация</span>
            <span className="ml-confidence">{Math.round((pixel.ml_confidence ?? 0) * 100)}%</span>
          </div>
          <div className="ml-class-name">{pixel.ml_class_ru}</div>
          <div className="ml-bar">
            <div className="ml-bar-fill" style={{ width: `${Math.round((pixel.ml_confidence ?? 0) * 100)}%` }} />
          </div>
        </div>
      )}

      <div className="section-label">Спектральные индексы</div>
      {pixel?.demo && <div className="zone-error">Демонстрационные данные — не спутниковое измерение.</div>}
      <div className="indices-list">
        {INDICES.map(({ key, code, label }) => {
          const v = pixel ? (pixel[key] ?? null) : null
          const pct = v != null ? Math.max(2, Math.min(100, ((v + 1) / 2) * 100)) : 0
          const color = indexColor(key, v)
          return (
            <div className="index-row" key={key}>
              <div className="index-head">
                <span className="index-name">{label} <span className="index-code">{code}</span></span>
                <span className="index-val" style={{ color }}>{v != null ? v.toFixed(4) : '…'}</span>
              </div>
              <div className="index-track">
                <div className="index-fill" style={{ width: `${pct}%`, background: color }} />
              </div>
            </div>
          )
        })}
      </div>

      <div className="ai-block">
        <div className="ai-header">
          <div className="ai-pulse" />
          <span>AI — интерпретация</span>
        </div>
        <div className="ai-text" aria-live="polite">
          {loading ? (
            <span className="ai-loading">
              <span className="dot-1">.</span><span className="dot-2">.</span><span className="dot-3">.</span>
            </span>
          ) : (
            error || aiText || ''
          )}
        </div>
      </div>

      <ZoneStatsPanel
        stats={zoneStats} loading={zoneLoading} error={zoneError}
        geometry={zoneGeometry} activeLayer={activeLayer} period={zonePeriod}
      />
      <TransectChart data={transectData} loading={transectLoading} error={transectError} />
      <ChangeStatsPanel stats={changeStats} loading={changeLoading} error={changeError} />
    </aside>
  )
}
