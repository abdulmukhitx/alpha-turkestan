const MODES = [
  { id: 'overview', label: 'Обзор', hint: 'Точки и зоны', icon: 'map' },
  { id: 'compare', label: 'Сравнение', hint: 'Два периода', icon: 'compare' },
  { id: 'change', label: 'Изменения', hint: 'Динамика', icon: 'change' },
  { id: 'forecast', label: 'Прогноз', hint: 'Сценарий тренда', icon: 'forecast' },
]

function ModeIcon({ name }) {
  if (name === 'compare') {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <rect x="2.5" y="3" width="6" height="14" rx="1.5" />
        <rect x="11.5" y="3" width="6" height="14" rx="1.5" />
      </svg>
    )
  }
  if (name === 'change') {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M3 6h11m0 0-3-3m3 3-3 3M17 14H6m0 0 3 3m-3-3 3-3" />
      </svg>
    )
  }
  if (name === 'forecast') {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M3 16h14M4 14l4-4 3 2 5-7m-4 0h4v4" />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="m3 5 4-2 6 2 4-2v12l-4 2-6-2-4 2V5Z" />
      <path d="M7 3v12m6-10v12" />
    </svg>
  )
}

function PanelIcon({ name }) {
  if (name === 'results') {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M4 16V9m6 7V4m6 12v-5" />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M4 5h12M4 10h12M4 15h12" />
      <circle cx="7" cy="5" r="1.5" />
      <circle cx="13" cy="10" r="1.5" />
      <circle cx="8" cy="15" r="1.5" />
    </svg>
  )
}

export default function WorkspaceNav({
  activeMode,
  onModeChange,
  forecastAvailable,
  leftPanelAvailable = true,
  leftPanelOpen,
  onToggleLeftPanel,
  rightPanelOpen,
  onToggleRightPanel,
  hasResults,
}) {
  return (
    <nav className="workspace-nav" aria-label="Режим работы и панели">
      <div className="mode-group" aria-label="Режим работы">
        <span className="mode-group-label">Режим</span>
        <div className="mode-list">
          {MODES.map((mode) => {
            const disabled = mode.id === 'forecast' && !forecastAvailable
            return (
              <button
                key={mode.id}
                type="button"
                className={`mode-button ${activeMode === mode.id ? 'active' : ''}`}
                aria-pressed={activeMode === mode.id}
                disabled={disabled}
                title={disabled ? 'Для прогноза нужны минимум три годовых периода' : mode.hint}
                onClick={() => onModeChange(mode.id)}
              >
                <span className="mode-icon"><ModeIcon name={mode.icon} /></span>
                <span className="mode-copy">
                  <strong>{mode.label}</strong>
                  <small>{mode.hint}</small>
                </span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="panel-toggle-group" aria-label="Панели рабочего пространства">
        <button
          type="button"
          className={`panel-toggle ${leftPanelOpen ? 'active' : ''}`}
          aria-expanded={leftPanelOpen}
          aria-controls="layer-panel"
          disabled={!leftPanelAvailable}
          title={!leftPanelAvailable ? 'Слои настраиваются отдельно для каждой стороны сравнения' : undefined}
          onClick={onToggleLeftPanel}
        >
          <PanelIcon name="layers" />
          <span>Слои</span>
        </button>
        <button
          type="button"
          className={`panel-toggle ${rightPanelOpen ? 'active' : ''}`}
          aria-expanded={rightPanelOpen}
          aria-controls="analysis-panel"
          onClick={onToggleRightPanel}
        >
          <PanelIcon name="results" />
          <span>Результаты</span>
          {hasResults && <span className="result-indicator" aria-label="Есть новые результаты" />}
        </button>
      </div>
    </nav>
  )
}
