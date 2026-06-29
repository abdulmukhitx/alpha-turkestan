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

export default function LayerPanel({
  layers, activeLayer, onSelect, opacity, onOpacityChange,
  drawMode, onToggleDraw, onClearZone, onFinishDraw, hasZone, drawPointCount = 0,
}) {
  const indexIds = Object.keys(layers).length ? Object.keys(layers) : Object.keys(NAMES).filter((id) => id !== 'satellite')
  const ids = ['satellite', ...indexIds.filter((id) => id !== 'satellite')]
  const active = layers[activeLayer]

  return (
    <aside className="panel panel-left">
      <div className="panel-header">
        <span className="panel-eyebrow">Мониторинг</span>
        <h2 className="panel-title">Индексы</h2>
      </div>

      <div className="section-label">Активный слой</div>
      <div className="layer-list">
        {ids.map((id) => {
          const [name, desc] = NAMES[id] || [id.toUpperCase(), '']
          return (
            <button
              key={id}
              className={`layer-btn ${id === activeLayer ? 'active' : ''}`}
              onClick={() => onSelect(id)}
            >
              <div className="layer-swatch" style={{ background: SWATCH[id] }} />
              <div className="layer-info">
                <span className="layer-name">{name}</span>
                <span className="layer-desc">{desc}</span>
              </div>
              <div className="layer-badge">{id === activeLayer ? '●' : ''}</div>
            </button>
          )
        })}
      </div>

      <div className="section-label" style={{ marginTop: 20 }}>Зональная статистика</div>
      <div className="zone-tools">
        <button
          className={`zone-tool-btn ${drawMode ? 'active' : ''}`}
          onClick={onToggleDraw}
        >
          {drawMode ? 'Отменить рисование' : '✏ Нарисовать зону'}
        </button>
        {drawMode && (
          <>
            <div className="zone-hint">
              Точки: {drawPointCount}. Кликните по первой точке или нажмите «Завершить», либо дважды кликните по карте.
            </div>
            <button
              className="zone-tool-btn zone-tool-finish"
              onClick={onFinishDraw}
              disabled={drawPointCount < 3}
            >
              ✓ Завершить ({drawPointCount})
            </button>
          </>
        )}
        {hasZone && (
          <button className="zone-tool-btn zone-tool-clear" onClick={onClearZone}>Очистить</button>
        )}
      </div>

      <div className="section-label" style={{ marginTop: 20 }}>Прозрачность</div>
      <div className="opacity-row">
        <input
          type="range" className="slider" min="0" max="100"
          value={Math.round(opacity * 100)}
          onChange={(e) => onOpacityChange(Number(e.target.value) / 100)}
        />
        <span className="opacity-val">{Math.round(opacity * 100)}%</span>
      </div>

      {active && active.range && (
        <div className="colorbar-wrap">
          <div className="colorbar-title">{(NAMES[activeLayer] || [activeLayer])[0]}</div>
          <div className="colorbar" style={{ background: active.cssGradient }} />
          <div className="colorbar-labels">
            {(LABELS[activeLayer] || [active.range[0], 0, active.range[1]]).map((t, i) => (
              <span key={i}>{t}</span>
            ))}
          </div>
        </div>
      )}
    </aside>
  )
}
