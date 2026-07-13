const SWATCH = {
  satellite: 'linear-gradient(90deg,#1b3a2b,#3a7a4f,#8fbf6f)',
  ndvi: 'linear-gradient(90deg,#a50026,#fdae61,#ffffbf,#a6d96a,#1a9850)',
  ndwi: 'linear-gradient(90deg,#b2182b,#f7f7f7,#2166ac)',
  ndre: 'linear-gradient(90deg,#d73027,#fee08b,#1a9850)',
  ndmi: 'linear-gradient(90deg,#67001f,#f4a582,#f7f7f7,#92c5de,#053061)',
  bsi:  'linear-gradient(90deg,#fff5eb,#fdd0a2,#fd8d3c,#d94801,#7f2704)',
  savi: 'linear-gradient(90deg,#a50026,#fdae61,#ffffbf,#a6d96a,#1a9850)',
  nbr:  'linear-gradient(90deg,#a50026,#fdae61,#ffffbf,#a6d96a,#1a9850)',
}

const NAMES = {
  satellite: ['Снимок', 'Спутниковый снимок'],
  ndvi: ['NDVI', 'Растительность'],
  ndwi: ['NDWI', 'Водные ресурсы'],
  ndre: ['NDRE', 'Стресс растений'],
  ndmi: ['NDMI', 'Влажность почвы'],
  bsi:  ['BSI', 'Голая почва'],
  savi: ['SAVI', 'Покрытие растительностью'],
  nbr:  ['NBR', 'Деградация территорий'],
}

// Descriptive 3-stop labels (low / mid / high) shown under the colorbar
const LABELS = {
  ndvi: ['Пустыня', 'Разреженная', 'Густая'],
  ndwi: ['Сухо', 'Норма', 'Вода'],
  ndre: ['Стресс', '', 'Здоровая'],
  ndmi: ['Сухая', 'Норма', 'Влажная'],
  bsi:  ['Покрытая', '', 'Голая почва'],
  savi: ['Пустыня', 'Разреженная', 'Густая'],
  nbr:  ['Деградация', '', 'Здоровая'],
}

function ToolIcon({ type }) {
  if (type === 'line') {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M3 15 7.5 10.5l3 2L17 5" />
        <circle cx="3" cy="15" r="1.3" /><circle cx="7.5" cy="10.5" r="1.3" />
        <circle cx="10.5" cy="12.5" r="1.3" /><circle cx="17" cy="5" r="1.3" />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="m4 6 5-3 7 4-2 8-7 2-4-5 1-6Z" />
      <circle cx="4" cy="6" r="1.2" /><circle cx="9" cy="3" r="1.2" />
      <circle cx="16" cy="7" r="1.2" /><circle cx="14" cy="15" r="1.2" />
      <circle cx="7" cy="17" r="1.2" /><circle cx="3" cy="12" r="1.2" />
    </svg>
  )
}

export default function LayerPanel({
  layers, activeLayer, onSelect, opacity, onOpacityChange,
  drawMode, onToggleDraw, onClearZone, onFinishDraw, hasZone, drawPointCount = 0,
  lineDrawMode, onToggleLineDraw, onClearLine, onFinishLineDraw, hasLine, lineDrawPointCount = 0,
  lineDisabled = false,
  forecastMode = false,
  onClose,
}) {
  const indexIds = Object.keys(layers).length ? Object.keys(layers) : Object.keys(NAMES).filter((id) => id !== 'satellite')
  const ids = indexIds.filter((id) => id !== 'satellite')
  const active = layers[activeLayer]

  return (
    <aside className="panel panel-left" id="layer-panel" aria-label="Слои и инструменты">
      <div className="panel-header panel-header-row">
        <div>
          <span className="panel-eyebrow">{forecastMode ? 'Экспериментальный режим' : 'Карта и данные'}</span>
          <h2 className="panel-title">Слои и инструменты</h2>
        </div>
        <button className="panel-close" type="button" onClick={onClose} aria-label="Скрыть панель слоёв">×</button>
      </div>

      <section className="panel-section" aria-labelledby="layers-heading">
        <div className="section-heading" id="layers-heading">
          <span>Данные карты</span>
          <span className="section-count">{ids.length + 1}</span>
        </div>
        <button
          type="button"
          className={`layer-btn base-layer-btn ${activeLayer === 'satellite' ? 'active' : ''}`}
          onClick={() => onSelect('satellite')}
          disabled={forecastMode}
          aria-pressed={activeLayer === 'satellite'}
          title={forecastMode ? 'Для прогноза выберите спектральный индекс' : undefined}
        >
          <span className="layer-swatch" style={{ background: SWATCH.satellite }} />
          <span className="layer-info">
            <span className="layer-name">Снимок Sentinel-2</span>
            <span className="layer-desc">Естественные цвета</span>
          </span>
          <span className="layer-badge" aria-hidden="true">{activeLayer === 'satellite' ? '✓' : ''}</span>
        </button>

        <div className="index-layer-grid">
        {ids.map((id) => {
          const [name, desc] = NAMES[id] || [id.toUpperCase(), '']
          return (
            <button
              key={id}
              type="button"
              className={`layer-btn index-layer-btn ${id === activeLayer ? 'active' : ''}`}
              onClick={() => onSelect(id)}
              aria-pressed={id === activeLayer}
              title={`${name} — ${desc}`}
            >
              <span className="layer-swatch" style={{ background: SWATCH[id] }} />
              <span className="layer-info">
                <span className="layer-name">{name}</span>
                <span className="layer-desc">{desc}</span>
              </span>
              <span className="layer-badge" aria-hidden="true">{id === activeLayer ? '✓' : ''}</span>
            </button>
          )
        })}
        </div>
      </section>

      {forecastMode ? (
        <div className="forecast-mode-hint">
          <strong>Прототип линейного тренда</strong>
          <span>Выберите индекс и кликните по карте. Это сценарий продолжения наблюдаемой тенденции.</span>
        </div>
      ) : (
        <section className="panel-section" aria-labelledby="analysis-tools-heading">
          <div className="section-heading" id="analysis-tools-heading">Инструменты анализа</div>
          <div className="analysis-tool-grid">
            <button
              type="button"
              className={`analysis-tool-button ${drawMode ? 'active' : ''}`}
              onClick={onToggleDraw}
              aria-pressed={drawMode}
            >
              <ToolIcon type="zone" />
              <span><strong>Зона</strong><small>{drawMode ? 'Отменить' : 'Площадь и классы'}</small></span>
            </button>
            <button
              type="button"
              className={`analysis-tool-button ${lineDrawMode ? 'active' : ''}`}
              onClick={onToggleLineDraw}
              disabled={lineDisabled}
              aria-pressed={lineDrawMode}
              title={lineDisabled ? 'Выберите спектральный индекс для построения профиля' : undefined}
            >
              <ToolIcon type="line" />
              <span><strong>Профиль</strong><small>{lineDrawMode ? 'Отменить' : 'Срез по линии'}</small></span>
            </button>
          </div>

          {(drawMode || lineDrawMode) && (
            <div className="draw-workflow" role="status">
              <div className="draw-workflow-copy">
                <strong>{drawMode ? 'Постройте границу зоны' : 'Проведите линию профиля'}</strong>
                <span>
                  {drawMode
                    ? `Точек: ${drawPointCount}. Минимум 3 точки.`
                    : `Точек: ${lineDrawPointCount}. Минимум 2 точки.`}
                </span>
              </div>
              <button
                type="button"
                className="zone-tool-btn zone-tool-finish"
                onClick={drawMode ? onFinishDraw : onFinishLineDraw}
                disabled={drawMode ? drawPointCount < 3 : lineDrawPointCount < 2}
              >
                Завершить
              </button>
            </div>
          )}

          {(hasZone || hasLine) && (
            <div className="clear-actions">
              {hasZone && <button type="button" onClick={onClearZone}>Очистить зону</button>}
              {hasLine && <button type="button" onClick={onClearLine}>Очистить линию</button>}
            </div>
          )}
        </section>
      )}

      <section className="panel-section display-section" aria-labelledby="display-heading">
        <div className="section-heading" id="display-heading">
          <span>Отображение</span>
          <span className="opacity-val">{Math.round(opacity * 100)}%</span>
        </div>
        <label className="sr-only" htmlFor="layer-opacity">Прозрачность слоя</label>
        <div className="opacity-row">
          <input
            id="layer-opacity" type="range" className="slider" min="0" max="100"
            value={Math.round(opacity * 100)}
            aria-valuetext={`${Math.round(opacity * 100)}%`}
            onChange={(e) => onOpacityChange(Number(e.target.value) / 100)}
          />
        </div>

        {active && active.range && (
          <div className="colorbar-wrap">
            <div className="colorbar-title">Легенда · {(NAMES[activeLayer] || [activeLayer])[0]}</div>
            <div className="colorbar" style={{ background: active.cssGradient }} />
            <div className="colorbar-labels">
              {(LABELS[activeLayer] || [active.range[0], 0, active.range[1]]).map((t, i) => (
                <span key={i}>{t}</span>
              ))}
            </div>
          </div>
        )}
      </section>
    </aside>
  )
}
