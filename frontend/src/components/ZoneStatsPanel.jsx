import ZoneReport from './ZoneReport.jsx'

const INDEX_LABELS = {
  ndvi: ['NDVI', 'Растительность'],
  ndwi: ['NDWI', 'Водные ресурсы'],
  ndre: ['NDRE', 'Стресс растений'],
  ndmi: ['NDMI', 'Влажность почвы'],
  bsi:  ['BSI',  'Голая почва'],
  savi: ['SAVI', 'Покрытие раст.'],
  nbr:  ['NBR',  'Деградация'],
}
const INDEX_ORDER = ['ndvi', 'ndre', 'ndwi', 'ndmi', 'bsi', 'savi', 'nbr']

const LULC_LABELS = {
  agriculture:       ['🟢', 'Сельхоз угодья'],
  bare_soil:         ['🟡', 'Голая почва'],
  dense_vegetation:  ['🌿', 'Густая раст.'],
  sparse_vegetation: ['🌾', 'Разреж. раст.'],
  urban:             ['🏙️', 'Застройка'],
  water:             ['💧', 'Вода'],
}
const LULC_COLORS = {
  agriculture:       '#4ade80',
  bare_soil:         '#fbbf24',
  dense_vegetation:  '#16a34a',
  sparse_vegetation: '#86efac',
  urban:             '#94a3b8',
  water:             '#60a5fa',
}
const LULC_ORDER = ['agriculture', 'dense_vegetation', 'sparse_vegetation', 'bare_soil', 'urban', 'water']

function indexBarColor(mean, key) {
  if (key === 'savi') {
    if (mean > 0.15) return '#16a34a'
    if (mean >= 0) return '#eab308'
    return '#ef4444'
  }
  if (key === 'nbr') {
    if (mean > 0) return '#16a34a'
    if (mean >= -0.15) return '#eab308'
    return '#ef4444'
  }
  if (mean > 0.2) return '#16a34a'
  if (mean >= 0) return '#eab308'
  return '#ef4444'
}

export default function ZoneStatsPanel({ stats, loading, error, geometry, activeLayer }) {
  if (!loading && !error && !stats) return null

  return (
    <div className="zone-stats">
      <div className="section-label" style={{ marginTop: 20 }}>Зональная статистика</div>

      {loading && (
        <div className="zone-loading">
          <span className="ai-loading">
            <span className="dot-1">.</span><span className="dot-2">.</span><span className="dot-3">.</span>
          </span>
          <span>Анализируем зону...</span>
        </div>
      )}

      {!loading && error && (
        <div className="zone-error">{error}</div>
      )}

      {!loading && !error && stats && (
        <>
          <div className="zone-area">
            Площадь: <strong>{stats.area_ha.toLocaleString('ru-RU')} га</strong>
            <span className="zone-area-px"> ({stats.pixel_count.toLocaleString('ru-RU')} пикс)</span>
          </div>

          <div className="zone-block">
            <div className="zone-block-title">Индексы</div>
            <div className="indices-list">
              {INDEX_ORDER.filter((k) => stats.indices[k]).map((key) => {
                const s = stats.indices[key]
                const [code, label] = INDEX_LABELS[key]
                const pct = Math.max(2, Math.min(100, ((s.mean + 1) / 2) * 100))
                const color = indexBarColor(s.mean, key)
                return (
                  <div className="index-row" key={key}>
                    <div className="index-head">
                      <span className="index-name">{label} <span className="index-code">{code}</span></span>
                      <span className="index-val" style={{ color }}>{s.mean.toFixed(3)}</span>
                    </div>
                    <div className="index-track">
                      <div className="index-fill" style={{ width: `${pct}%`, background: color }} />
                    </div>
                    <div className="zone-index-range">
                      min: {s.min.toFixed(2)} &nbsp; max: {s.max.toFixed(2)} &nbsp; σ: {s.std.toFixed(2)}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {stats.lulc && Object.keys(stats.lulc).length > 0 && (
            <div className="zone-block">
              <div className="zone-block-title">Классификация земель</div>

              <div className="zone-lulc-bar">
                {LULC_ORDER.filter((k) => stats.lulc[k]).map((key) => (
                  <div
                    key={key}
                    className="zone-lulc-seg"
                    style={{ width: `${stats.lulc[key].percent}%`, background: LULC_COLORS[key] }}
                    title={`${LULC_LABELS[key]?.[1] || key}: ${stats.lulc[key].percent}%`}
                  />
                ))}
              </div>

              <div className="zone-lulc-list">
                {LULC_ORDER.filter((k) => stats.lulc[k] && stats.lulc[k].pixels > 0).map((key) => {
                  const v = stats.lulc[key]
                  const [icon, label] = LULC_LABELS[key] || ['', key]
                  return (
                    <div className="zone-lulc-row" key={key}>
                      <span className="zone-lulc-icon">{icon}</span>
                      <span className="zone-lulc-name">{label}</span>
                      <span className="zone-lulc-area">{v.area_ha.toLocaleString('ru-RU')} га</span>
                      <span className="zone-lulc-pct">{v.percent}%</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          <ZoneReport geometry={geometry} stats={stats} activeLayer={activeLayer} />
        </>
      )}
    </div>
  )
}
