const INDEX_CODES = {
  ndvi: 'NDVI', ndwi: 'NDWI', ndre: 'NDRE', ndmi: 'NDMI',
  bsi: 'BSI', savi: 'SAVI', nbr: 'NBR',
}

const DIRECTION_COLORS = {
  improving: '#22c55e',
  degrading: '#ef4444',
  stable: '#eab308',
}

export default function ForecastPanel({ point, result, loading, error, targetYear, index, onClose }) {
  const trend = result?.trend
  const code = INDEX_CODES[result?.index || index] || (result?.index || index || '').toUpperCase()
  const color = DIRECTION_COLORS[trend?.direction] || 'var(--text2)'

  return (
    <aside className="panel panel-right" id="analysis-panel" aria-label="Результаты прогноза">
      <div className="panel-header panel-header-row">
        <div>
          <span className="panel-eyebrow">Экспериментальный прогноз</span>
          <h2 className="panel-title">Линейный тренд · {targetYear}</h2>
        </div>
        <button className="panel-close" type="button" onClick={onClose} aria-label="Скрыть панель результатов">×</button>
      </div>

      {!point ? (
        <div className="click-prompt">
          <svg className="click-icon" width="30" height="30" viewBox="0 0 32 32" fill="none">
            <path d="M5 24 12 17l5 4 10-13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            <path d="m21 8 6 0 0 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <p className="click-text">Кликните по карте, чтобы рассчитать локальный прогноз выбранного индекса.</p>
        </div>
      ) : (
        <>
          <div className="result-location">
            <span>{point.lat.toFixed(4)}°N · {point.lng.toFixed(4)}°E · {code}</span>
          </div>

          {loading && <div className="forecast-loading" role="status">Расчёт тренда…</div>}
          {error && <div className="zone-error">{error}</div>}

          {trend && !loading && (
            <>
              <div className="forecast-summary-grid">
                <div className="forecast-summary-card">
                  <span>Последнее значение</span>
                  <strong>{trend.latest_value.toFixed(4)}</strong>
                </div>
                <div className="forecast-summary-card forecast-summary-primary">
                  <span>Прогноз {result.target_year}</span>
                  <strong>{trend.predicted.toFixed(4)}</strong>
                </div>
              </div>

              <div className="forecast-direction" style={{ borderColor: color }}>
                <span style={{ color }}>{trend.direction_ru}</span>
                <strong style={{ color }}>
                  {trend.change_from_latest >= 0 ? '+' : ''}{trend.change_from_latest.toFixed(4)}
                </strong>
              </div>

              <div className="section-label">Наблюдаемая история</div>
              <div className="forecast-history">
                {result.history.map((item) => (
                  <div className="forecast-history-row" key={item.period}>
                    <span>{item.year}</span>
                    <div className="forecast-history-line" />
                    <strong>{item.value.toFixed(4)}</strong>
                  </div>
                ))}
                <div className="forecast-history-row forecast-history-future">
                  <span>{result.target_year}</span>
                  <div className="forecast-history-line" />
                  <strong>{trend.predicted.toFixed(4)}</strong>
                </div>
              </div>

              <div className="forecast-metrics">
                <div><span>Тренд в год</span><strong>{trend.slope_per_year >= 0 ? '+' : ''}{trend.slope_per_year.toFixed(4)}</strong></div>
                <div><span>Диапазон чувствительности</span><strong>{trend.sensitivity_low.toFixed(4)} … {trend.sensitivity_high.toFixed(4)}</strong></div>
                <div><span>Качество линии</span><strong>{trend.trend_quality === 'consistent' ? 'устойчивый' : 'изменчивый'}</strong></div>
                <div><span>R² на истории</span><strong>{trend.r_squared.toFixed(3)}</strong></div>
              </div>

              <div className="forecast-disclaimer">{result.disclaimer}</div>
            </>
          )}
        </>
      )}
    </aside>
  )
}
