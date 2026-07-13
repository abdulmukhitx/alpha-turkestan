const INDEX_CODES = {
  ndvi: 'NDVI', ndwi: 'NDWI', ndre: 'NDRE', ndmi: 'NDMI',
  bsi: 'BSI', savi: 'SAVI', nbr: 'NBR',
}

export default function ForecastBar({ open, config, targetYear, onTargetYearChange, activeIndex }) {
  const minYear = config?.min_target_year
  const maxYear = config?.max_target_year
  const targetYears = minYear && maxYear
    ? Array.from({ length: maxYear - minYear + 1 }, (_, i) => minYear + i)
    : []
  const sourceYears = config?.source_years?.join(' · ') || 'нет данных'

  return (
    <div className={`change-bar forecast-bar ${open ? 'open' : ''}`}>
      <div className="change-bar-inner">
        <div className="change-bar-title"><span className="workflow-dot forecast-dot" />Прогноз</div>

        <div className="change-bar-controls">
          <label className="change-bar-field">
            <span>Целевой год</span>
            <select
              value={targetYear || ''}
              onChange={(e) => onTargetYearChange(Number(e.target.value))}
              aria-label="Целевой год прогноза"
            >
              {targetYears.map((year) => <option key={year} value={year}>{year}</option>)}
            </select>
          </label>
          <div className="change-bar-sep" />
          <div className="forecast-bar-metric">
            <span>Индекс</span>
            <strong>{INDEX_CODES[activeIndex] || activeIndex?.toUpperCase()}</strong>
          </div>
          <div className="forecast-bar-metric">
            <span>История</span>
            <strong>{sourceYears}</strong>
          </div>
        </div>

        <div className="forecast-bar-note">
          Экспериментальный сценарий продолжения тренда — не ML-прогноз
        </div>
      </div>
    </div>
  )
}
