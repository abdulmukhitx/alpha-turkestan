import { useI18n } from '../i18n.jsx'

export default function TopBar({
  lat, lon, health, onRefresh, periods, period, onPeriodChange, periodDisabled = false,
  account, accountLoading = false, onAccountOpen, onLocaleChange,
}) {
  const { locale, setLocale, t, periodLabel } = useI18n()
  const online = health && ['ok', 'degraded'].includes(health.status)
  const degraded = health?.status === 'degraded'
  const statusText = health
    ? (online
        ? `Sentinel-2 · ${health.cog ? 'COG mosaic' : t('top.tiles', { count: health.s2_tiles })}${degraded ? ` · ${t('top.degraded')}` : ''}`
        : t('top.offline'))
    : t('top.connecting')

  function changeLocale(nextLocale) {
    setLocale(nextLocale)
    onLocaleChange?.(nextLocale)
  }

  return (
    <header className="topbar">
      <div className="topbar-left">
        <div className="brand-mark" aria-hidden="true">
          <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
            <path d="M4 9.5 14 4l10 5.5v9L14 24 4 18.5v-9Z" stroke="currentColor" strokeWidth="1.3" />
            <path d="m4 9.5 10 5.5 10-5.5M14 15v9" stroke="currentColor" strokeWidth="1.3" />
            <circle cx="14" cy="15" r="2.6" fill="currentColor" />
          </svg>
        </div>
        <div className="brand-copy">
          <span className="brand-name">GeoAI<span className="brand-dot">·</span>TKO</span>
          <span className="brand-subtitle">{t('brand.subtitle')}</span>
        </div>
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
          <label className="period-control">
            <span>{t('top.period')}</span>
            <select
              className="period-select"
              value={period}
              onChange={(e) => onPeriodChange(e.target.value)}
              disabled={periodDisabled}
              aria-label={t('top.periodAria')}
              title={periodDisabled ? t('top.periodForecastDisabled') : t('top.periodAria')}
            >
              {periods.map((p) => (
                <option key={p.period_id} value={p.period_id}>{periodLabel(p)}</option>
              ))}
            </select>
          </label>
        )}
        <label className="language-control">
          <span className="sr-only">{t('top.language')}</span>
          <select value={locale} onChange={(event) => changeLocale(event.target.value)} aria-label={t('top.language')}>
            <option value="ru">RU</option>
            <option value="kk">ҚАЗ</option>
            <option value="en">EN</option>
          </select>
        </label>
        <div className={`status-pill ${online ? (degraded ? 'degraded' : '') : 'offline'}`} role="status" aria-live="polite">
          <span className="status-dot" />
          <span>{statusText}</span>
        </div>
        <button className="icon-btn" type="button" title={t('top.refresh')} aria-label={t('top.refresh')} onClick={onRefresh}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M2 8a6 6 0 1 1 1.5 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            <path d="M2 12V8h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <button
          data-account-entry="true"
          className={`account-entry ${account ? 'authenticated' : ''}`}
          type="button"
          onClick={onAccountOpen}
          disabled={accountLoading}
          aria-label={account ? t('top.profile', { name: account.user.display_name }) : t('top.signIn')}
        >
          <span className="account-avatar" aria-hidden="true">
            {account ? account.user.display_name.slice(0, 1).toUpperCase() : (
              <svg viewBox="0 0 20 20">
                <circle cx="10" cy="7" r="3" />
                <path d="M4.5 17c.5-3 2.3-4.6 5.5-4.6s5 1.6 5.5 4.6" />
              </svg>
            )}
          </span>
          <span className="account-entry-copy">
            <strong>{accountLoading ? t('top.checking') : (account?.user.display_name || t('top.signInShort'))}</strong>
            <small>{account ? (account.user.email_verified ? t('top.syncing') : t('top.unverified')) : t('top.accountStorage')}</small>
          </span>
        </button>
      </div>
    </header>
  )
}
