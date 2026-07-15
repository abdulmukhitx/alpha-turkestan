import { NavLink } from 'react-router'
import { useI18n } from '../i18n.jsx'

const NAV_ITEMS = [
  { to: '/dashboard', label: 'pages.dashboard' },
  { to: '/work', label: 'pages.work' },
  { to: '/map', label: 'pages.map' },
  { to: '/history', label: 'pages.history' },
]

export default function ProductHeader({ account }) {
  const { locale, setLocale, t } = useI18n()
  return (
    <header className="product-header">
      <NavLink className="product-brand" to="/dashboard" aria-label={t('pages.dashboard')}>
        <span className="brand-mark" aria-hidden="true">
          <svg width="26" height="26" viewBox="0 0 28 28" fill="none">
            <path d="M4 9.5 14 4l10 5.5v9L14 24 4 18.5v-9Z" stroke="currentColor" strokeWidth="1.3" />
            <path d="m4 9.5 10 5.5 10-5.5M14 15v9" stroke="currentColor" strokeWidth="1.3" />
            <circle cx="14" cy="15" r="2.6" fill="currentColor" />
          </svg>
        </span>
        <span><strong>GeoAI·TKO</strong><small>{t('brand.subtitle')}</small></span>
      </NavLink>

      <nav className="product-nav" aria-label={t('pages.navigation')}>
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            viewTransition
            className={({ isActive }) => isActive ? 'active' : ''}
          >
            {t(item.label)}
          </NavLink>
        ))}
      </nav>

      <div className="product-header-actions">
        <label className="language-control">
          <span className="sr-only">{t('top.language')}</span>
          <select value={locale} onChange={(event) => setLocale(event.target.value)} aria-label={t('top.language')}>
            <option value="ru">RU</option>
            <option value="kk">ҚАЗ</option>
            <option value="en">EN</option>
          </select>
        </label>
        <NavLink className="product-account" to={account ? '/map?account=profile' : '/map?account=login'}>
          <span>{account?.user?.display_name?.slice(0, 1).toUpperCase() || '○'}</span>
          <strong>{account?.user?.display_name || t('top.signInShort')}</strong>
        </NavLink>
      </div>
    </header>
  )
}
