const SWATCH = {
  rgb:  'linear-gradient(90deg,#444,#bbb,#fff)',
  ndvi: 'linear-gradient(90deg,#a50026,#fdae61,#ffffbf,#a6d96a,#1a9850)',
  ndwi: 'linear-gradient(90deg,#f7fbff,#74a9cf,#0570b0,#023858)',
  ndre: 'linear-gradient(90deg,#f7fcf5,#c7e9c0,#41ab5d,#00441b)',
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
            <span>{active.range[0]}</span>
            <span>0</span>
            <span>{active.range[1]}</span>
          </div>
        </div>
      )}
    </aside>
  )
}
