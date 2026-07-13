const CHANGE_INDEX_OPTIONS = [
  { key: 'ndvi', code: 'NDVI', label: 'Растительность' },
  { key: 'ndwi', code: 'NDWI', label: 'Водные ресурсы' },
  { key: 'ndre', code: 'NDRE', label: 'Стресс растений' },
  { key: 'ndmi', code: 'NDMI', label: 'Влажность почвы' },
  { key: 'bsi',  code: 'BSI',  label: 'Голая почва' },
  { key: 'savi', code: 'SAVI', label: 'Покрытие раст.' },
  { key: 'nbr',  code: 'NBR',  label: 'Деградация' },
]

export default function ChangeDetectionBar({
  open, periods, periodBefore, periodAfter, onPeriodBeforeChange, onPeriodAfterChange,
  index, onIndexChange,
  drawMode, onToggleDraw, onFinishDraw, onClearZone, hasZone, drawPointCount = 0,
}) {
  const samePeriod = !!periodBefore && !!periodAfter && periodBefore === periodAfter

  return (
    <div className={`change-bar ${open ? 'open' : ''}`}>
      <div className="change-bar-inner">
        <div className="change-bar-title"><span className="workflow-dot" />Изменения</div>

        <div className="change-bar-controls">
          <label className="change-bar-field">
            <span>От</span>
            <select value={periodBefore || ''} onChange={(e) => onPeriodBeforeChange(e.target.value)}>
              {periods.map((p) => <option key={p.period_id} value={p.period_id}>{p.label}</option>)}
            </select>
          </label>
          <span className="change-bar-arrow">→</span>
          <label className="change-bar-field">
            <span>До</span>
            <select value={periodAfter || ''} onChange={(e) => onPeriodAfterChange(e.target.value)}>
              {periods.map((p) => <option key={p.period_id} value={p.period_id}>{p.label}</option>)}
            </select>
          </label>

          <div className="change-bar-sep" />

          <label className="change-bar-field">
            <span>Индекс</span>
            <select value={index} onChange={(e) => onIndexChange(e.target.value)}>
              {CHANGE_INDEX_OPTIONS.map((opt) => (
                <option key={opt.key} value={opt.key}>{opt.code} — {opt.label}</option>
              ))}
            </select>
          </label>
        </div>

        {samePeriod ? (
          <div className="change-bar-error">Выберите разные периоды</div>
        ) : (
          <div className="change-bar-legend">
            <span className="change-bar-legend-label">Деградация</span>
            <div className="change-bar-legend-bar" />
            <span className="change-bar-legend-label">Улучшение</span>
          </div>
        )}

        <div className="change-bar-actions">
          <button
            className={`zone-tool-btn ${drawMode ? 'active' : ''}`}
            onClick={onToggleDraw}
            disabled={samePeriod}
          >
            {drawMode ? 'Отменить рисование' : 'Определить зону'}
          </button>
          {drawMode && (
            <button
              className="zone-tool-btn zone-tool-finish"
              onClick={onFinishDraw}
              disabled={drawPointCount < 3}
            >
              ✓ Завершить ({drawPointCount})
            </button>
          )}
          {hasZone && (
            <button className="zone-tool-btn zone-tool-clear" onClick={onClearZone}>Очистить</button>
          )}
        </div>
      </div>
    </div>
  )
}
