const INDEX_LABELS = {
  ndvi: ['NDVI', 'Растительность'],
  ndre: ['NDRE', 'Стресс растений'],
  ndwi: ['NDWI', 'Водные ресурсы'],
  ndmi: ['NDMI', 'Влажность почвы'],
  bsi:  ['BSI',  'Голая почва'],
  savi: ['SAVI', 'Покрытие раст.'],
  nbr:  ['NBR',  'Деградация'],
}
const INDEX_ORDER = ['ndvi', 'ndre', 'ndwi', 'ndmi', 'bsi', 'savi', 'nbr']

const LULC_LABELS_RU = {
  agriculture:       'Сельхоз угодья',
  bare_soil:         'Голая почва',
  dense_vegetation:  'Густая раст.',
  sparse_vegetation: 'Разреж. раст.',
  urban:             'Застройка',
  water:             'Вода',
}
const LULC_ICONS = {
  agriculture:       '🟢',
  bare_soil:         '🟡',
  dense_vegetation:  '🌿',
  sparse_vegetation: '🌾',
  urban:             '🏙️',
  water:             '💧',
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

function directionArrow(direction) {
  if (direction === 'улучшение') return '↑'
  if (direction === 'деградация') return '↓'
  return '→'
}
function directionColor(direction) {
  if (direction === 'улучшение') return '#16a34a'
  if (direction === 'деградация') return '#ef4444'
  return 'var(--text3)'
}

// Urban and bare_soil are spectrally similar (low vegetation, high brightness) —
// classifier flips between them across two mosaics are often noise from seasonal/
// atmospheric differences rather than real construction/abandonment. Flag it
// explicitly so officials don't read these numbers as ground truth.
function isNoisyPair(from, to) {
  return (from === 'urban' && to === 'bare_soil') || (from === 'bare_soil' && to === 'urban')
}

function ChangeLulcBars({ matrix, netChange }) {
  if (!matrix) return null

  const beforeByClass = {}
  const afterByClass = {}
  for (const from of LULC_ORDER) {
    if (!matrix[from]) continue
    beforeByClass[from] = Object.values(matrix[from]).reduce((a, b) => a + b, 0)
    for (const to of LULC_ORDER) {
      afterByClass[to] = (afterByClass[to] || 0) + (matrix[from][to] || 0)
    }
  }
  const total = Object.values(beforeByClass).reduce((a, b) => a + b, 0) || 1

  return (
    <div className="change-lulc-bars">
      <div className="change-lulc-bar-row">
        <span className="change-lulc-bar-label">2023</span>
        <div className="zone-lulc-bar">
          {LULC_ORDER.filter((k) => beforeByClass[k] > 0).map((k) => (
            <div
              key={k} className="zone-lulc-seg"
              style={{ width: `${(beforeByClass[k] / total) * 100}%`, background: LULC_COLORS[k] }}
              title={LULC_LABELS_RU[k]}
            />
          ))}
        </div>
      </div>
      <div className="change-lulc-bar-row">
        <span className="change-lulc-bar-label">2025</span>
        <div className="zone-lulc-bar">
          {LULC_ORDER.filter((k) => afterByClass[k] > 0).map((k) => (
            <div
              key={k} className="zone-lulc-seg"
              style={{ width: `${(afterByClass[k] / total) * 100}%`, background: LULC_COLORS[k] }}
              title={LULC_LABELS_RU[k]}
            />
          ))}
        </div>
      </div>

      {netChange && (
        <div className="zone-lulc-list" style={{ marginTop: 8 }}>
          {LULC_ORDER.filter((k) => netChange[k] !== undefined).map((k) => (
            <div className="zone-lulc-row" key={k}>
              <span className="zone-lulc-icon">{LULC_ICONS[k]}</span>
              <span className="zone-lulc-name">{LULC_LABELS_RU[k]}</span>
              <span
                className="zone-lulc-area"
                style={{ color: netChange[k] > 0 ? '#16a34a' : netChange[k] < 0 ? '#ef4444' : 'var(--text3)' }}
              >
                {netChange[k] > 0 ? '+' : ''}{netChange[k].toLocaleString('ru-RU')} га
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function ChangeStatsPanel({ stats, loading, error }) {
  if (!loading && !error && !stats) return null

  const hasNoisyTop = stats?.ml_transitions?.top_changes?.some((t) => isNoisyPair(t.from, t.to))

  return (
    <div className="change-stats">
      <div className="section-label" style={{ marginTop: 20 }}>📊 Изменения 2023 → 2025</div>

      {loading && (
        <div className="zone-loading">
          <span className="ai-loading">
            <span className="dot-1">.</span><span className="dot-2">.</span><span className="dot-3">.</span>
          </span>
          <span>Анализируем изменения...</span>
        </div>
      )}

      {!loading && error && <div className="zone-error">{error}</div>}

      {!loading && !error && stats && (
        <>
          <div className="zone-area">
            Площадь: <strong>{stats.area_ha.toLocaleString('ru-RU')} га</strong>
            <span className="zone-area-px"> ({stats.pixel_count.toLocaleString('ru-RU')} пикс)</span>
          </div>

          <div className="zone-block">
            <div className="zone-block-title">Индексы</div>
            <div className="change-index-list">
              {INDEX_ORDER.filter((k) => stats.indices?.[k]).map((key) => {
                const s = stats.indices[key]
                const [code, label] = INDEX_LABELS[key]
                const pctChange = s.mean_before ? (s.delta / Math.abs(s.mean_before)) * 100 : null
                return (
                  <div className="change-index-row" key={key}>
                    <div className="change-index-head">
                      <span className="index-name">{label} <span className="index-code">{code}</span></span>
                      <span className="change-index-delta" style={{ color: directionColor(s.direction) }}>
                        {directionArrow(s.direction)} {s.delta > 0 ? '+' : ''}{s.delta.toFixed(3)}
                        {pctChange != null && ` (${pctChange > 0 ? '+' : ''}${pctChange.toFixed(0)}%)`}
                      </span>
                    </div>
                    <div className="change-index-sub">
                      было: {s.mean_before.toFixed(3)} &nbsp; стало: {s.mean_after.toFixed(3)}
                      &nbsp;·&nbsp; значимо на {s.significant_pct.toFixed(1)}% площади
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {stats.ml_transitions && (
            <div className="zone-block">
              <div className="zone-block-title">Классификация земель</div>

              {stats.ml_transitions.top_changes?.length > 0 && (
                <div className="change-top-list">
                  {stats.ml_transitions.top_changes.map((t, i) => (
                    <div key={i}>
                      <div className="change-top-row">
                        <span className="change-top-icon">{LULC_ICONS[t.from] || ''}→{LULC_ICONS[t.to] || ''}</span>
                        <span className="change-top-name">
                          {LULC_LABELS_RU[t.from] || t.from} → {LULC_LABELS_RU[t.to] || t.to}
                        </span>
                        <span className="change-top-area">{t.area_ha.toLocaleString('ru-RU')} га</span>
                      </div>
                      {isNoisyPair(t.from, t.to) && (
                        <div className="change-disclaimer">
                          ⚠️ Переходы между застройкой и голой почвой могут содержать шум классификатора — рекомендуется перекрёстная проверка
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {!hasNoisyTop && (stats.ml_transitions.matrix?.urban?.bare_soil > 0 || stats.ml_transitions.matrix?.bare_soil?.urban > 0) && (
                <div className="change-disclaimer">
                  ⚠️ Переходы между застройкой и голой почвой могут содержать шум классификатора — рекомендуется перекрёстная проверка
                </div>
              )}

              <div className="change-block-subtitle">Площадь по классам: было vs стало</div>
              <ChangeLulcBars matrix={stats.ml_transitions.matrix} netChange={stats.ml_transitions.net_change_ha} />
            </div>
          )}

          {stats.groq_analysis && (
            <div className="ai-block">
              <div className="ai-header"><div className="ai-pulse" /><span>AI — анализ изменений</span></div>
              <div className="ai-text">{stats.groq_analysis}</div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
