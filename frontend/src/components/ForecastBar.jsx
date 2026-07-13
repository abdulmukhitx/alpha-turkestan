const INDEX_CODES = {
  ndvi: 'NDVI', ndwi: 'NDWI', ndre: 'NDRE', ndmi: 'NDMI',
  bsi: 'BSI', savi: 'SAVI', nbr: 'NBR',
}

export default function ForecastBar({ open, config, targetYear, onTargetYearChange, activeIndex }) {
  const { t } = useI18n()
  const minYear = config?.min_target_year
  const maxYear = config?.max_target_year
  const targetYears = minYear && maxYear
    ? Array.from({ length: maxYear - minYear + 1 }, (_, i) => minYear + i)
    : []
  const sourceYears = config?.source_years?.join(' · ') || t('forecast.noData')

  return (
    <div className={`change-bar forecast-bar ${open ? 'open' : ''}`}>
      <div className="change-bar-inner">
        <div className="change-bar-title"><span className="workflow-dot forecast-dot" />{t('forecast.title')}</div>

        <div className="change-bar-controls">
          <label className="change-bar-field">
            <span>{t('forecast.targetYear')}</span>
            <select
              value={targetYear || ''}
              onChange={(e) => onTargetYearChange(Number(e.target.value))}
              aria-label={t('forecast.yearAria')}
            >
              {targetYears.map((year) => <option key={year} value={year}>{year}</option>)}
            </select>
          </label>
          <div className="change-bar-sep" />
          <div className="forecast-bar-metric">
            <span>{t('forecast.index')}</span>
            <strong>{INDEX_CODES[activeIndex] || activeIndex?.toUpperCase()}</strong>
          </div>
          <div className="forecast-bar-metric">
            <span>{t('forecast.history')}</span>
            <strong>{sourceYears}</strong>
          </div>
        </div>

        <div className="forecast-bar-note">
          {t('forecast.experimental')}
        </div>
      </div>
    </div>
  )
}
import { useI18n } from '../i18n.jsx'
