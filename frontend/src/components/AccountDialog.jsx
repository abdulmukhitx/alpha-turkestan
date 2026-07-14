import { useEffect, useMemo, useRef, useState } from 'react'
import { useI18n } from '../i18n.jsx'
import AccountSecurityPanel from './AccountSecurityPanel.jsx'
import AnalysisHistoryPanel from './AnalysisHistoryPanel.jsx'
import GoogleSignInButton from './GoogleSignInButton.jsx'

const LAYER_OPTIONS = [
  ['satellite', 'layer.satellite'],
  ['ndvi', 'index.ndvi'],
  ['ndwi', 'index.ndwi'],
  ['ndre', 'index.ndre'],
  ['ndmi', 'index.ndmi'],
  ['bsi', 'index.bsi'],
  ['savi', 'index.savi'],
  ['nbr', 'index.nbr'],
]

const EMPTY_AUTH = { display_name: '', email: '', password: '' }

function downloadJson(payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `geoai-tko-account-${new Date().toISOString().slice(0, 10)}.json`
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

export default function AccountDialog({
  open, initialView = 'login', initialNotice = '', initialError = '', resetToken = '', account, authConfig, periods,
  onClose, onLogin, onRegister, onSaveProfile, onLogout, onExport, onDeleteAccount,
  onGoogleLogin, onGoogleLink, onRefreshAuthConfig,
  onForgotPassword, onResetPassword, onResendVerification,
  onChangePassword, onFetchSessions, onRevokeSession, onRevokeOtherSessions,
  analysisRefreshKey, onFetchAnalyses, onDeleteAnalysis,
}) {
  const { locale, t, formatDate, periodLabel } = useI18n()
  const [view, setView] = useState(initialView)
  const [authForm, setAuthForm] = useState(EMPTY_AUTH)
  const [profileForm, setProfileForm] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [saved, setSaved] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deletePassword, setDeletePassword] = useState('')
  const [developmentLink, setDevelopmentLink] = useState('')
  const dialogRef = useRef(null)
  const busyRef = useRef(busy)
  const onCloseRef = useRef(onClose)
  busyRef.current = busy
  onCloseRef.current = onClose

  const availablePeriodIds = useMemo(() => new Set(periods.map((item) => item.period_id)), [periods])

  useEffect(() => {
    if (!open) return
    setView(account ? 'profile' : initialView)
    setError(initialError)
    setNotice(initialNotice)
    setSaved(false)
    setDeleteOpen(false)
    setDeletePassword('')
    setDevelopmentLink('')
    if (account) {
      const preferences = account.preferences || {}
      setProfileForm({
        display_name: account.user.display_name,
        locale: preferences.locale || locale,
        timezone: preferences.timezone || 'Asia/Qyzylorda',
        default_layer: preferences.default_layer || 'ndvi',
        default_basemap: preferences.default_basemap || 'satellite',
        default_period: availablePeriodIds.has(preferences.default_period)
          ? preferences.default_period
          : (periods.at(-1)?.period_id || '2025_summer'),
        default_opacity: preferences.default_opacity ?? 0.85,
        left_panel_open: preferences.left_panel_open !== false,
        right_panel_open: preferences.right_panel_open === true,
        threshold_alerts: preferences.threshold_alerts || [],
      })
    } else {
      setAuthForm(EMPTY_AUTH)
    }
    // Do not depend on the complete account object: a save replaces it and
    // would erase the controlled form and success feedback.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, account?.user.id, initialView, initialNotice, initialError, availablePeriodIds, periods])

  useEffect(() => {
    if (!open) return undefined
    const previouslyFocused = document.activeElement
    const frame = window.requestAnimationFrame(() => {
      const focusable = dialogRef.current?.querySelector('input:not(:disabled), select:not(:disabled)')
        || dialogRef.current?.querySelector('button:not(:disabled), a[href]')
      focusable?.focus()
    })
    function handleKey(event) {
      if (event.key === 'Escape' && !busyRef.current) {
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab' || !dialogRef.current) return
      const focusable = [...dialogRef.current.querySelectorAll(
        'button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href], iframe'
      )].filter((element) => element.offsetParent !== null)
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => {
      window.cancelAnimationFrame(frame)
      window.removeEventListener('keydown', handleKey)
      const fallback = document.querySelector('[data-account-entry="true"]')
      const restoreTarget = previouslyFocused instanceof HTMLElement
        && previouslyFocused !== document.body
        && previouslyFocused.isConnected
        ? previouslyFocused
        : fallback
      restoreTarget?.focus()
    }
  }, [open])

  if (!open) return null

  function setAuthField(field, value) {
    setAuthForm((current) => ({ ...current, [field]: value }))
  }

  function setProfileField(field, value) {
    setProfileForm((current) => ({ ...current, [field]: value }))
    setSaved(false)
  }

  function showDelivery(result, successKey) {
    const delivery = result?.delivery || result?.verification_delivery
    if (delivery?.sent === false) {
      setNotice('')
      setDevelopmentLink('')
      setError(t('error.emailDelivery'))
      return false
    }
    setError('')
    setNotice(t(successKey))
    setDevelopmentLink(delivery?.preview_url || '')
    return true
  }

  function localizedError(requestError, fallbackKey) {
    return requestError?.code ? t(requestError.code) : (requestError?.message || t(fallbackKey))
  }

  function accountDate(value) {
    if (!value) return t('account.notAvailable')
    const date = new Date(value)
    return Number.isNaN(date.getTime())
      ? value
      : formatDate(date, { dateStyle: 'medium', timeStyle: 'short' })
  }

  async function submitAuth(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setNotice('')
    try {
      if (view === 'register') {
        const result = await onRegister({ ...authForm, locale })
        if (result?.verification_delivery) showDelivery(result, 'account.verificationSent')
      } else if (view === 'recover') {
        const result = await onForgotPassword({ email: authForm.email, locale })
        showDelivery(result, 'account.recoveryAccepted')
      } else if (view === 'reset') {
        await onResetPassword({ token: resetToken, password: authForm.password })
        setNotice(t('account.passwordReset'))
      } else {
        await onLogin({ email: authForm.email, password: authForm.password, locale })
      }
    } catch (requestError) {
      const fallback = view === 'recover' ? t('error.recovery') : view === 'reset' ? t('error.reset') : t('error.login')
      setError(requestError?.code ? t(requestError.code) : (requestError.message || fallback))
    } finally {
      setBusy(false)
    }
  }

  async function submitProfile(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setSaved(false)
    try {
      const { display_name, ...preferences } = profileForm
      await onSaveProfile(display_name, preferences)
      setSaved(true)
    } catch (requestError) {
      setError(localizedError(requestError, 'error.profileSave'))
    } finally {
      setBusy(false)
    }
  }

  async function handleExport() {
    setBusy(true)
    setError('')
    try {
      downloadJson(await onExport())
    } catch (requestError) {
      setError(localizedError(requestError, 'error.export'))
    } finally {
      setBusy(false)
    }
  }

  async function handleLogout() {
    setBusy(true)
    setError('')
    try {
      await onLogout()
    } catch (requestError) {
      setError(localizedError(requestError, 'error.logout'))
      setBusy(false)
    }
  }

  async function handleResendVerification() {
    setBusy(true)
    setError('')
    try {
      const result = await onResendVerification()
      showDelivery(result, 'account.verificationSent')
    } catch (requestError) {
      setError(localizedError(requestError, 'error.resend'))
    } finally {
      setBusy(false)
    }
  }

  async function handleDelete() {
    if (!window.confirm(t('account.confirmDelete'))) return
    setBusy(true)
    setError('')
    try {
      await onDeleteAccount(account.user.has_password === false ? null : deletePassword)
    } catch (requestError) {
      setError(localizedError(requestError, 'error.deleteAccount'))
      setBusy(false)
    }
  }

  async function handleGoogleCredential(credential, linking = false) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      if (linking) {
        await onGoogleLink(credential)
        setNotice(t('account.googleConnected'))
      } else {
        const result = await onGoogleLogin(credential)
        if (result?.verification_delivery) {
          showDelivery(result, 'account.verificationSent')
        }
      }
    } catch (requestError) {
      setError(localizedError(requestError, linking ? 'error.googleLink' : 'error.googleLogin'))
    } finally {
      setBusy(false)
    }
  }

  async function retryGoogleConfiguration() {
    setBusy(true)
    setError('')
    try {
      const config = await onRefreshAuthConfig()
      if (!config?.google?.enabled || !config?.google?.client_id) {
        setError(t('error.googleRestartRequired'))
      }
    } catch {
      setError(t('error.googleUnavailable'))
    } finally {
      setBusy(false)
    }
  }

  const authTitle = view === 'register'
    ? t('account.create')
    : view === 'recover'
      ? t('account.recoverTitle')
      : view === 'reset'
        ? t('account.newPasswordTitle')
        : t('account.login')
  const googleEnabled = Boolean(authConfig?.google?.enabled && authConfig?.google?.client_id)
  const googleLinked = account?.user?.auth_methods?.includes('google') === true
  const hasPassword = account?.user?.has_password !== false

  return (
    <div className="account-overlay" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <section ref={dialogRef} className="account-dialog" role="dialog" aria-modal="true" aria-labelledby="account-dialog-title">
        <button type="button" className="account-dialog-close" onClick={onClose} disabled={busy} aria-label={t('common.close')}>×</button>

        {account && profileForm ? (
          <>
            <div className="account-dialog-heading">
              <div className="account-avatar large" aria-hidden="true">{account.user.display_name.slice(0, 1).toUpperCase()}</div>
              <div>
                <span className="account-eyebrow">{t('account.personal')}</span>
                <h2 id="account-dialog-title">{t('account.title')}</h2>
                <p>{account.user.email}</p>
              </div>
            </div>

            <div className="account-profile-meta">
              <span>{t('account.memberSince')} <strong>{accountDate(account.user.created_at)}</strong></span>
              <span>{t('account.lastLogin')} <strong>{accountDate(account.user.last_login_at)}</strong></span>
            </div>

            <div className={`account-verification ${account.user.email_verified ? 'verified' : ''}`}>
              <strong>{account.user.email_verified ? `✓ ${t('account.verified')}` : t('top.unverified')}</strong>
              {!account.user.email_verified && (
                <>
                  <p>{t('account.verifyNotice')}</p>
                  <button type="button" onClick={handleResendVerification} disabled={busy}>{t('account.resendVerification')}</button>
                </>
              )}
            </div>

            <form className="account-profile-form" onSubmit={submitProfile}>
              <div className="account-form-section">
                <h3>{t('account.profile')}</h3>
                <label>
                  <span>{t('account.name')}</span>
                  <input value={profileForm.display_name} minLength={2} maxLength={80} autoComplete="name" onChange={(event) => setProfileField('display_name', event.target.value)} required />
                </label>
                <div className="account-form-grid">
                  <label>
                    <span>{t('account.language')}</span>
                    <select value={profileForm.locale} onChange={(event) => setProfileField('locale', event.target.value)}>
                      <option value="ru">{t('language.ru')}</option>
                      <option value="kk">{t('language.kk')}</option>
                      <option value="en">{t('language.en')}</option>
                    </select>
                  </label>
                  <label>
                    <span>{t('account.timezone')}</span>
                    <select value={profileForm.timezone} onChange={(event) => setProfileField('timezone', event.target.value)}>
                      <option value="Asia/Qyzylorda">{t('account.qyzylorda')}</option>
                      <option value="Asia/Almaty">{t('account.almaty')}</option>
                      <option value="UTC">UTC</option>
                    </select>
                  </label>
                </div>
              </div>

              <div className="account-form-section">
                <h3>{t('account.loginMap')}</h3>
                <div className="account-form-grid">
                  <label>
                    <span>{t('account.layer')}</span>
                    <select value={profileForm.default_layer} onChange={(event) => setProfileField('default_layer', event.target.value)}>
                      {LAYER_OPTIONS.map(([id, key]) => <option key={id} value={id}>{id === 'satellite' ? t(key) : `${id.toUpperCase()} · ${t(key)}`}</option>)}
                    </select>
                  </label>
                  <label>
                    <span>{t('account.basemap')}</span>
                    <select value={profileForm.default_basemap} onChange={(event) => setProfileField('default_basemap', event.target.value)}>
                      <option value="satellite">{t('map.basemapSatellite')}</option>
                      <option value="terrain">{t('map.basemapTerrain')}</option>
                      <option value="dark">{t('map.basemapDark')}</option>
                    </select>
                  </label>
                </div>
                <label>
                  <span>{t('account.period')}</span>
                  <select value={profileForm.default_period} onChange={(event) => setProfileField('default_period', event.target.value)}>
                    {periods.map((item) => <option key={item.period_id} value={item.period_id}>{periodLabel(item)}</option>)}
                  </select>
                </label>
                <label>
                  <span>{t('layer.opacity')} · {Math.round(profileForm.default_opacity * 100)}%</span>
                  <input type="range" min="0" max="100" value={Math.round(profileForm.default_opacity * 100)} onChange={(event) => setProfileField('default_opacity', Number(event.target.value) / 100)} />
                </label>
                <div className="account-checkboxes">
                  <label><input type="checkbox" checked={profileForm.left_panel_open} onChange={(event) => setProfileField('left_panel_open', event.target.checked)} />{t('account.openLayers')}</label>
                  <label><input type="checkbox" checked={profileForm.right_panel_open} onChange={(event) => setProfileField('right_panel_open', event.target.checked)} />{t('account.openResults')}</label>
                </div>
              </div>

              {error && <div className="account-message error" role="alert">{error}</div>}
              {(saved || notice) && <div className="account-message success" role="status">{saved ? t('account.saved') : notice}</div>}
              {developmentLink && <a className="account-dev-link" href={developmentLink}>{t('account.devLink')} →</a>}

              <div className="account-primary-actions">
                <button type="submit" className="account-primary" disabled={busy}>{t('account.save')}</button>
                <button type="button" className="account-secondary" onClick={handleLogout} disabled={busy}>{t('account.logout')}</button>
              </div>
            </form>

            {(googleEnabled || googleLinked) && (
              <section className="account-google-connection" aria-labelledby="account-google-title">
                <div>
                  <h3 id="account-google-title">{t('account.googleSignIn')}</h3>
                  <p>{googleLinked ? t('account.googleLinked') : t('account.googleLinkHelp')}</p>
                </div>
                {googleLinked ? (
                  <span className="account-provider-connected">✓ {t('account.connected')}</span>
                ) : (
                  <GoogleSignInButton
                    clientId={authConfig.google.client_id}
                    locale={locale}
                    disabled={busy}
                    onCredential={(credential) => handleGoogleCredential(credential, true)}
                    onError={() => setError(t('error.googleUnavailable'))}
                  />
                )}
              </section>
            )}

            <AccountSecurityPanel
              hasPassword={hasPassword}
              onChangePassword={onChangePassword}
              onFetchSessions={onFetchSessions}
              onRevokeSession={onRevokeSession}
              onRevokeOtherSessions={onRevokeOtherSessions}
            />

            <AnalysisHistoryPanel
              refreshKey={analysisRefreshKey}
              onFetch={onFetchAnalyses}
              onDelete={onDeleteAnalysis}
            />

            <div className="account-data-actions">
              <button type="button" onClick={handleExport} disabled={busy}>{t('account.export')}</button>
              <button type="button" className="danger-link" onClick={() => setDeleteOpen((value) => !value)} disabled={busy}>{t('account.delete')}</button>
            </div>
            {deleteOpen && (
              <div className="account-delete-box">
                <strong>{t('account.deletePermanent')}</strong>
                <p>{hasPassword ? t('account.deleteHelp') : t('account.deleteGoogleHelp')}</p>
                {hasPassword && <input type="password" autoComplete="current-password" value={deletePassword} onChange={(event) => setDeletePassword(event.target.value)} placeholder={t('account.currentPassword')} />}
                <button type="button" onClick={handleDelete} disabled={busy || (hasPassword && !deletePassword)}>{t('account.deleteForever')}</button>
              </div>
            )}
          </>
        ) : (
          <>
            <div className="account-dialog-heading compact">
              <div className="account-avatar large" aria-hidden="true">G</div>
              <div>
                <span className="account-eyebrow">GeoAI · TKO</span>
                <h2 id="account-dialog-title">{authTitle}</h2>
                <p>{view === 'recover' ? t('account.recoverHelp') : t('account.syncPitch')}</p>
              </div>
            </div>

            <form className="account-auth-form" onSubmit={submitAuth}>
              {view === 'register' && (
                <label><span>{t('account.name')}</span><input autoFocus value={authForm.display_name} minLength={2} maxLength={80} autoComplete="name" onChange={(event) => setAuthField('display_name', event.target.value)} required /></label>
              )}
              {view !== 'reset' && (
                <label><span>{t('account.email')}</span><input autoFocus={view !== 'register'} type="email" value={authForm.email} maxLength={254} autoComplete="email" onChange={(event) => setAuthField('email', event.target.value)} required /></label>
              )}
              {view !== 'recover' && (
                <label>
                  <span>{view === 'reset' ? t('account.newPassword') : t('account.password')}</span>
                  <input type="password" value={authForm.password} minLength={view === 'login' ? 1 : 15} maxLength={128} autoComplete={view === 'login' ? 'current-password' : 'new-password'} onChange={(event) => setAuthField('password', event.target.value)} required />
                  {view !== 'login' && <small>{t('account.passwordHelp')}</small>}
                </label>
              )}
              {error && <div className="account-message error" role="alert">{error}</div>}
              {notice && <div className="account-message success" role="status">{notice}</div>}
              {developmentLink && <a className="account-dev-link" href={developmentLink}>{t('account.devLink')} →</a>}
              <button className="account-primary" type="submit" disabled={busy}>
                {busy ? t('common.wait') : view === 'register' ? t('account.createAndLogin') : view === 'recover' ? t('account.sendReset') : view === 'reset' ? t('account.resetPassword') : t('top.signInShort')}
              </button>
            </form>

            {(view === 'login' || view === 'register') && (
              <div className="account-google-auth">
                <div className="account-auth-divider"><span>{t('account.or')}</span></div>
                {googleEnabled ? (
                  <GoogleSignInButton
                    clientId={authConfig.google.client_id}
                    locale={locale}
                    disabled={busy}
                    onCredential={(credential) => handleGoogleCredential(credential, false)}
                    onError={() => setError(t('error.googleUnavailable'))}
                  />
                ) : (
                  <div className="account-google-unavailable" role="status">
                    <span className="account-google-mark" aria-hidden="true">G</span>
                    <span><strong>{t('account.googleSignIn')}</strong><small>{t('account.googleRestartHelp')}</small></span>
                    <button type="button" onClick={retryGoogleConfiguration} disabled={busy}>{t('account.retryGoogle')}</button>
                  </div>
                )}
              </div>
            )}

            {view === 'login' && <button type="button" className="account-switch-view" onClick={() => { setView('recover'); setError(''); setNotice('') }} disabled={busy}>{t('account.forgot')}</button>}
            {view !== 'reset' && (
              <button type="button" className="account-switch-view" onClick={() => { setView(view === 'register' ? 'login' : view === 'recover' ? 'login' : 'register'); setError(''); setNotice(''); setDevelopmentLink('') }} disabled={busy}>
                {view === 'register' ? t('account.haveAccount') : view === 'recover' ? t('account.backToLogin') : t('account.noAccount')}
              </button>
            )}
            <p className="account-guest-note">{t('account.guestNote')}</p>
          </>
        )}
      </section>
    </div>
  )
}
