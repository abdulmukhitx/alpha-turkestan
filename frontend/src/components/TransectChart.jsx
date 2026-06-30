import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip } from 'recharts'

const LAYER_LABELS = {
  ndvi: 'NDVI', ndwi: 'NDWI', ndre: 'NDRE', ndmi: 'NDMI', bsi: 'BSI', savi: 'SAVI', nbr: 'NBR',
}

const LAYER_COLORS = {
  ndvi: '#22c55e',
  savi: '#22c55e',
  nbr:  '#22c55e',
  ndwi: '#3b82f6',
  ndmi: '#3b82f6',
  ndre: '#a3e635',
  bsi:  '#f97316',
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null
  const v = payload[0].value
  return (
    <div className="transect-tooltip">
      <div>{Math.round(label).toLocaleString('ru-RU')} м</div>
      <div>{v != null ? v.toFixed(3) : 'нет данных'}</div>
    </div>
  )
}

export default function TransectChart({ data, loading, error }) {
  if (!loading && !error && !data) return null

  return (
    <div className="zone-stats">
      <div className="section-label" style={{ marginTop: 20 }}>Профиль по линии</div>

      {loading && (
        <div className="zone-loading">
          <span className="ai-loading">
            <span className="dot-1">.</span><span className="dot-2">.</span><span className="dot-3">.</span>
          </span>
          <span>Строим профиль...</span>
        </div>
      )}

      {!loading && error && (
        <div className="zone-error">{error}</div>
      )}

      {!loading && !error && data && (
        <div className="zone-block transect-block">
          <div className="transect-header">
            <span className="zone-block-title">{LAYER_LABELS[data.layer] || data.layer.toUpperCase()}</span>
            <span className="transect-length">Длина: {data.total_length_m.toLocaleString('ru-RU')} м</span>
          </div>

          <div className="transect-chart-wrap">
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={data.points} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="transect-fill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={LAYER_COLORS[data.layer] || '#3b82f6'} stopOpacity={0.35} />
                    <stop offset="95%" stopColor={LAYER_COLORS[data.layer] || '#3b82f6'} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
                <XAxis
                  dataKey="distance_m"
                  tick={{ fill: 'var(--text3)', fontSize: 9 }}
                  tickFormatter={(v) => `${Math.round(v)}`}
                  stroke="var(--border)"
                />
                <YAxis tick={{ fill: 'var(--text3)', fontSize: 9 }} stroke="var(--border)" width={36} />
                <Tooltip content={<CustomTooltip />} />
                <Area
                  type="monotone"
                  dataKey="value"
                  stroke={LAYER_COLORS[data.layer] || '#3b82f6'}
                  strokeWidth={2}
                  fill="url(#transect-fill)"
                  dot={false}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          <div className="transect-stats">
            <span>Мин: <strong>{data.stats.min.toFixed(3)}</strong></span>
            <span>Среднее: <strong>{data.stats.mean.toFixed(3)}</strong></span>
            <span>Макс: <strong>{data.stats.max.toFixed(3)}</strong></span>
          </div>
        </div>
      )}
    </div>
  )
}
