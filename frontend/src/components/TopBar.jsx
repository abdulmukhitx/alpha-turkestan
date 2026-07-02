export default function TopBar({ lat, lon, health, onRefresh, periods, period, onPeriodChange }) {
  const online = health && health.status === 'ok'
  const statusText = health
    ? (online ? `Sentinel-2 · ${health.cog ? 'COG mosaic' : health.s2_tiles + ' tiles'}` : 'Backend offline')
    : 'Подключение...'

  return (
    <header className="topbar">
      <div className="topbar-left">
        <div className="brand-mark">
          <svg width="24" height="24" viewBox="0 0 28 28" fill="none">
            <circle cx="14" cy="14" r="12" stroke="currentColor" strokeWidth="1.4" strokeDasharray="3 2" />
            <circle cx="14" cy="14" r="3" fill="currentColor" />
          </svg>
        </div>
        <span className="brand-name">GeoAI<span className="brand-dot">·</span>TKO</span>
      </div>

      <div className="topbar-center">
        <div className="coord-display">
          <span className="coord-label">LAT</span>
          <span className="coord-val">{lat != null ? `${lat.toFixed(4)}°N` : '—'}</span>
          <span className="coord-sep">·</span>
          <span className="coord-label">LON</span>
          <span className="coord-val">{lon != null ? `${lon.toFixed(4)}°E` : '—'}</span>
        </div>
      </div>

      <div className="topbar-right">
        {periods?.length > 0 && (
          <select
            className="period-select"
            value={period}
            onChange={(e) => onPeriodChange(e.target.value)}
            title="Период съёмки"
          >
            {periods.map((p) => (
              <option key={p.period_id} value={p.period_id}>{p.label}</option>
            ))}
          </select>
        )}
        <div className={`status-pill ${online ? '' : 'offline'}`}>
          <span className="status-dot" />
          <span>{statusText}</span>
        </div>
        <button className="icon-btn" title="Обновить данные" onClick={onRefresh}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M2 8a6 6 0 1 1 1.5 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            <path d="M2 12V8h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
    </header>
  )
}
