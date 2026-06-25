const SWATCH = {
  rgb:  'linear-gradient(90deg,#444,#bbb,#fff)',
  ndvi: 'linear-gradient(90deg,#a50026,#fdae61,#ffffbf,#a6d96a,#1a9850)',
  ndwi: 'linear-gradient(90deg,#b2182b,#f7f7f7,#2166ac)',
  ndre: 'linear-gradient(90deg,#d73027,#fee08b,#1a9850)',
  ndmi: 'linear-gradient(90deg,#67001f,#f4a582,#f7f7f7,#92c5de,#053061)',
  bsi:  'linear-gradient(90deg,#fff5eb,#fdd0a2,#fd8d3c,#d94801,#7f2704)',
}

const NAMES = {
  rgb:  ['RGB', 'Снимок'],
  ndvi: ['NDVI', 'Растительность'],
  ndwi: ['NDWI', 'Водные ресурсы'],
  ndre: ['NDRE', 'Стресс растений'],
  ndmi: ['NDMI', 'Влажность почвы'],
  bsi:  ['BSI', 'Голая почва'],
}

// Descriptive 3-stop labels (low / mid / high) shown under the colorbar
const LABELS = {
  ndvi: ['Пустыня', 'Разреженная', 'Густая'],
  ndwi: ['Сухо', 'Норма', 'Вода'],
  ndre: ['Стресс', '', 'Здоровая'],
  ndmi: ['Сухая', 'Норма', 'Влажная'],
  bsi:  ['Покрытая', '', 'Голая почва'],
}

export default function LayerPanel({ layers, activeLayer, onSelect, opacity, onOpacityChange }) {
  const ids = Object.keys(layers).length ? Object.keys(layers) : Object.keys(NAMES)
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
