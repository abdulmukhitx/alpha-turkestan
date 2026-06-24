import { useEffect, useState } from 'react'

function Typewriter({ text }) {
  const [shown, setShown] = useState('')

  useEffect(() => {
    setShown('')
    if (!text) return
    let i = 0
    const id = setInterval(() => {
      i += 1
      setShown(text.slice(0, i))
      if (i >= text.length) clearInterval(id)
    }, 18)
    return () => clearInterval(id)
  }, [text])

  return <>{shown}</>
}

function classify(ndvi, ndwi) {
  if (ndvi == null) return ['—', '—']
  if (ndwi != null && ndwi > 0.2) return ['Водная поверхность', '→ стабильно']
  if (ndvi > 0.50) return ['Густая растительность', '↑ активный рост']
  if (ndvi > 0.30) return ['Ирригированное поле', '↑ хорошее состояние']
  if (ndvi > 0.15) return ['Пастбище', '→ умеренное']
  if (ndvi > 0.05) return ['Деградирующие земли', '↓ требует мониторинга']
  return ['Голая почва / пустыня', '→ минимальная активность']
}

export default function AnalysisPanel({ point, pixel, aiText, loading, error }) {
  if (!point) {
    return (
      <aside className="panel panel-right">
        <div className="panel-header">
          <span className="panel-eyebrow">AI-интерпретация</span>
          <h2 className="panel-title">Анализ точки</h2>
        </div>
        <div className="click-prompt">
          <svg className="click-icon" width="30" height="30" viewBox="0 0 32 32" fill="none">
            <circle cx="16" cy="16" r="14" stroke="currentColor" strokeWidth="1" strokeDasharray="3 2" opacity="0.5" />
            <circle cx="16" cy="16" r="6" stroke="currentColor" strokeWidth="1.5" />
            <circle cx="16" cy="16" r="2" fill="currentColor" />
          </svg>
          <p className="click-text">Кликните на любую точку карты, чтобы получить AI-анализ этого места</p>
        </div>
      </aside>
    )
  }

  const ndvi = pixel?.ndvi ?? null
  const ndwi = pixel?.ndwi ?? null
  const [autoClass, autoTrend] = classify(ndvi, ndwi)
  const landClass = pixel?.land_class || autoClass
  const trend = pixel?.trend_label || autoTrend

  const ndviPct = ndvi != null ? Math.max(0, Math.min(100, ((ndvi + 0.2) / 1.0) * 100)) : 0
  const ndwiPct = ndwi != null ? Math.max(0, Math.min(100, ((ndwi + 0.5) / 1.1) * 100)) : 0

  return (
    <aside className="panel panel-right">
      <div className="panel-header">
        <span className="panel-eyebrow">AI-интерпретация</span>
        <h2 className="panel-title">Анализ точки</h2>
      </div>

      <div className="result-location">
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
          <circle cx="6" cy="5" r="3" stroke="currentColor" strokeWidth="1.2" />
          <path d="M6 9C6 9 2 6.5 2 5a4 4 0 0 1 8 0C10 6.5 6 9 6 9z" stroke="currentColor" strokeWidth="1.2" />
        </svg>
        <span>{point.lat.toFixed(4)}°N · {point.lng.toFixed(4)}°E</span>
      </div>

      <div className="metrics-grid">
        <div className="metric-card">
          <div className="metric-label">NDVI</div>
          <div className="metric-value">{ndvi != null ? ndvi.toFixed(2) : '…'}</div>
          <div className="metric-bar"><div className="metric-fill ndvi-fill" style={{ width: `${ndviPct}%` }} /></div>
        </div>
        <div className="metric-card">
          <div className="metric-label">NDWI</div>
          <div className="metric-value">{ndwi != null ? ndwi.toFixed(2) : '…'}</div>
          <div className="metric-bar"><div className="metric-fill ndwi-fill" style={{ width: `${ndwiPct}%` }} /></div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Класс</div>
          <div className="metric-value small">{pixel ? landClass : '…'}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Тренд</div>
          <div className="metric-value small">{pixel ? trend : '…'}</div>
        </div>
      </div>

      <div className="ai-block">
        <div className="ai-header">
          <div className="ai-pulse" />
          <span>AI — интерпретация</span>
        </div>
        <div className="ai-text">
          {loading ? (
            <span className="ai-loading">
              <span className="dot-1">.</span><span className="dot-2">.</span><span className="dot-3">.</span>
            </span>
          ) : (
            <Typewriter text={error || aiText || ''} />
          )}
        </div>
      </div>
    </aside>
  )
}
