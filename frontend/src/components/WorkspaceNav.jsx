import { useI18n } from '../i18n.jsx'
import { NavLink } from 'react-router'

const MODES = [
  { id: 'overview', label: 'nav.overview', hint: 'nav.overviewHint', icon: 'map' },
  { id: 'compare', label: 'nav.compare', hint: 'nav.compareHint', icon: 'compare' },
  { id: 'change', label: 'nav.change', hint: 'nav.changeHint', icon: 'change' },
  { id: 'forecast', label: 'nav.forecast', hint: 'nav.forecastHint', icon: 'forecast' },
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
  onShare,
  shareStatus,
}) {
  const { t } = useI18n()
  return (
    <nav className="workspace-nav" aria-label={t('nav.aria')}>
      <div className="mode-group" aria-label={t('nav.mode')}>
        <span className="mode-group-label">{t('nav.mode')}</span>
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
                title={disabled ? t('nav.forecastUnavailable') : t(mode.hint)}
                onClick={() => onModeChange(mode.id)}
              >
                <span className="mode-icon"><ModeIcon name={mode.icon} /></span>
                <span className="mode-copy">
                  <strong>{t(mode.label)}</strong>
                  <small>{t(mode.hint)}</small>
                </span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="panel-toggle-group" aria-label={t('nav.panels')}>
        <NavLink className="panel-toggle page-link" to="/dashboard" viewTransition>
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 3h6v6H3V3Zm8 0h6v6h-6V3ZM3 11h6v6H3v-6Zm8 0h6v6h-6v-6Z" /></svg>
          <span>{t('pages.dashboard')}</span>
        </NavLink>
        <NavLink className="panel-toggle page-link" to="/history" viewTransition>
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 4h12M4 8h12M4 12h8M4 16h6" /></svg>
          <span>{t('pages.history')}</span>
        </NavLink>
        <button type="button" className="panel-toggle" onClick={onShare} title={t('nav.shareHint')}>
          <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="5" cy="10" r="2" /><circle cx="15" cy="5" r="2" /><circle cx="15" cy="15" r="2" /><path d="m7 9 6-3m-6 5 6 3" /></svg>
          <span>{shareStatus || t('nav.share')}</span>
        </button>
        <button
          type="button"
          className={`panel-toggle ${leftPanelOpen ? 'active' : ''}`}
          aria-expanded={leftPanelOpen}
          aria-controls="layer-panel"
          disabled={!leftPanelAvailable}
          title={!leftPanelAvailable ? t('nav.compareLayers') : undefined}
          onClick={onToggleLeftPanel}
        >
          <PanelIcon name="layers" />
          <span>{t('nav.layers')}</span>
        </button>
        <button
          type="button"
          className={`panel-toggle ${rightPanelOpen ? 'active' : ''}`}
          aria-expanded={rightPanelOpen}
          aria-controls="analysis-panel"
          onClick={onToggleRightPanel}
        >
          <PanelIcon name="results" />
          <span>{t('nav.results')}</span>
          {hasResults && <span className="result-indicator" aria-label={t('nav.newResults')} />}
        </button>
      </div>
    </nav>
  )
}
