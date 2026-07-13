import { useI18n } from '../i18n.jsx'

const CHANGE_INDEX_OPTIONS = [
  { key: 'ndvi', code: 'NDVI' }, { key: 'ndwi', code: 'NDWI' },
  { key: 'ndre', code: 'NDRE' }, { key: 'ndmi', code: 'NDMI' },
  { key: 'bsi', code: 'BSI' }, { key: 'savi', code: 'SAVI' }, { key: 'nbr', code: 'NBR' },
]

export default function ChangeDetectionBar({
  open, periods, periodBefore, periodAfter, onPeriodBeforeChange, onPeriodAfterChange,
  index, onIndexChange,
  drawMode, onToggleDraw, onFinishDraw, onClearZone, hasZone, drawPointCount = 0,
}) {
  const { t, periodLabel } = useI18n()
  const samePeriod = !!periodBefore && !!periodAfter && periodBefore === periodAfter

  return (
    <div className={`change-bar ${open ? 'open' : ''}`}>
      <div className="change-bar-inner">
        <div className="change-bar-title"><span className="workflow-dot" />{t('change.title')}</div>

        <div className="change-bar-controls">
          <label className="change-bar-field">
            <span>{t('change.from')}</span>
            <select value={periodBefore || ''} onChange={(e) => onPeriodBeforeChange(e.target.value)}>
              {periods.map((p) => <option key={p.period_id} value={p.period_id}>{periodLabel(p)}</option>)}
            </select>
          </label>
          <span className="change-bar-arrow">→</span>
          <label className="change-bar-field">
            <span>{t('change.to')}</span>
            <select value={periodAfter || ''} onChange={(e) => onPeriodAfterChange(e.target.value)}>
              {periods.map((p) => <option key={p.period_id} value={p.period_id}>{periodLabel(p)}</option>)}
            </select>
          </label>

          <div className="change-bar-sep" />

          <label className="change-bar-field">
            <span>{t('change.index')}</span>
            <select value={index} onChange={(e) => onIndexChange(e.target.value)}>
              {CHANGE_INDEX_OPTIONS.map((opt) => (
                <option key={opt.key} value={opt.key}>{opt.code} — {t(`index.${opt.key}`)}</option>
              ))}
            </select>
          </label>
        </div>

        {samePeriod ? (
          <div className="change-bar-error">{t('change.chooseDifferent')}</div>
        ) : (
          <div className="change-bar-legend">
            <span className="change-bar-legend-label">{t('change.degradation')}</span>
            <div className="change-bar-legend-bar" />
            <span className="change-bar-legend-label">{t('change.improvement')}</span>
          </div>
        )}

        <div className="change-bar-actions">
          <button
            className={`zone-tool-btn ${drawMode ? 'active' : ''}`}
            onClick={onToggleDraw}
            disabled={samePeriod}
          >
            {drawMode ? t('change.cancelDraw') : t('change.draw')}
          </button>
          {drawMode && (
            <button
              className="zone-tool-btn zone-tool-finish"
              onClick={onFinishDraw}
              disabled={drawPointCount < 3}
            >
              ✓ {t('change.finish', { count: drawPointCount })}
            </button>
          )}
          {hasZone && (
            <button className="zone-tool-btn zone-tool-clear" onClick={onClearZone}>{t('change.clear')}</button>
          )}
        </div>
      </div>
    </div>
  )
}
