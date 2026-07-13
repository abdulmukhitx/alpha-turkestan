import { useEffect, useState } from 'react'
import { useI18n } from '../i18n.jsx'

const EMPTY_PASSWORDS = { current_password: '', new_password: '', confirm_password: '' }

export default function AccountSecurityPanel({
  hasPassword = true,
  onChangePassword,
  onFetchSessions,
  onRevokeSession,
  onRevokeOtherSessions,
}) {
  const { t, formatDate } = useI18n()
  const [passwords, setPasswords] = useState(EMPTY_PASSWORDS)
  const [sessions, setSessions] = useState([])
  const [loadingSessions, setLoadingSessions] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function loadSessions() {
    setLoadingSessions(true)
    try {
      const result = await onFetchSessions()
      setSessions(result.sessions || [])
    } catch (requestError) {
      setError(requestError?.code ? t(requestError.code) : (requestError.message || t('error.sessions')))
    } finally {
      setLoadingSessions(false)
    }
  }

  useEffect(() => {
    loadSessions()
    // The account dialog owns these callbacks for the life of this mounted panel.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function setPasswordField(field, value) {
    setPasswords((current) => ({ ...current, [field]: value }))
    setError('')
    setNotice('')
  }

  async function submitPassword(event) {
    event.preventDefault()
    if (passwords.new_password !== passwords.confirm_password) {
      setError(t('account.passwordMismatch'))
      return
    }
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await onChangePassword({
        current_password: passwords.current_password,
        new_password: passwords.new_password,
      })
      setPasswords(EMPTY_PASSWORDS)
      setNotice(t('account.passwordChanged'))
      await loadSessions()
    } catch (requestError) {
      setError(requestError?.code ? t(requestError.code) : (requestError.message || t('error.passwordChange')))
    } finally {
      setBusy(false)
    }
  }

  async function revokeSession(session) {
    const confirmKey = session.current ? 'account.revokeCurrentConfirm' : 'account.revokeSessionConfirm'
    if (!window.confirm(t(confirmKey))) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await onRevokeSession(session.id)
      if (!result.current) {
        setSessions((current) => current.filter((item) => item.id !== session.id))
        setNotice(t('account.sessionRevoked'))
      }
    } catch (requestError) {
      setError(requestError?.code ? t(requestError.code) : (requestError.message || t('error.sessionRevoke')))
    } finally {
      setBusy(false)
    }
  }

  async function revokeOthers() {
    if (!window.confirm(t('account.revokeOthersConfirm'))) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await onRevokeOtherSessions()
      setSessions((current) => current.filter((item) => item.current))
      setNotice(t('account.sessionsRevoked', { count: result.revoked_count }))
    } catch (requestError) {
      setError(requestError?.code ? t(requestError.code) : (requestError.message || t('error.sessionRevoke')))
    } finally {
      setBusy(false)
    }
  }

  function sessionDate(value) {
    const date = new Date(value)
    return Number.isNaN(date.getTime())
      ? value
      : formatDate(date, { dateStyle: 'medium', timeStyle: 'short' })
  }

  const otherSessionCount = sessions.filter((session) => !session.current).length

  return (
    <section className="account-security" aria-labelledby="account-security-title">
      <div className="account-security-heading">
        <div>
          <h3 id="account-security-title">{t('account.security')}</h3>
          <p>{t('account.securityHelp')}</p>
        </div>
      </div>

      {hasPassword ? (
        <form className="account-password-form" onSubmit={submitPassword}>
          <h4>{t('account.changePassword')}</h4>
          <label>
            <span>{t('account.currentPassword')}</span>
            <input type="password" autoComplete="current-password" value={passwords.current_password} onChange={(event) => setPasswordField('current_password', event.target.value)} required />
          </label>
          <div className="account-form-grid">
            <label>
              <span>{t('account.newPassword')}</span>
              <input type="password" minLength={15} maxLength={128} autoComplete="new-password" value={passwords.new_password} onChange={(event) => setPasswordField('new_password', event.target.value)} required />
            </label>
            <label>
              <span>{t('account.confirmNewPassword')}</span>
              <input type="password" minLength={15} maxLength={128} autoComplete="new-password" value={passwords.confirm_password} onChange={(event) => setPasswordField('confirm_password', event.target.value)} required />
            </label>
          </div>
          <small>{t('account.passwordHelp')}</small>
          <button type="submit" className="account-secondary" disabled={busy}>{busy ? t('common.wait') : t('account.changePassword')}</button>
        </form>
      ) : (
        <div className="account-password-managed">
          <h4>{t('account.passwordLogin')}</h4>
          <p>{t('account.passwordManagedGoogle')}</p>
        </div>
      )}

      <div className="account-sessions">
        <div className="account-sessions-heading">
          <div>
            <h4>{t('account.sessions')}</h4>
            <p>{t('account.sessionsHelp')}</p>
          </div>
          {otherSessionCount > 0 && <button type="button" onClick={revokeOthers} disabled={busy}>{t('account.revokeOthers')}</button>}
        </div>

        {loadingSessions ? (
          <p className="account-sessions-empty">{t('common.wait')}</p>
        ) : sessions.length === 0 ? (
          <p className="account-sessions-empty">{t('account.noSessions')}</p>
        ) : (
          <ul className="account-session-list">
            {sessions.map((session) => (
              <li key={session.id} className={session.current ? 'current' : ''}>
                <div>
                  <strong>{session.device}</strong>
                  {session.current && <span className="account-current-session">{t('account.currentSession')}</span>}
                  <p>{session.ip_address || t('account.unknownIp')} · {t('account.lastSeen')} {sessionDate(session.last_seen_at)}</p>
                </div>
                <button type="button" onClick={() => revokeSession(session)} disabled={busy}>
                  {session.current ? t('account.signOutSession') : t('account.revoke')}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {error && <div className="account-message error" role="alert">{error}</div>}
      {notice && <div className="account-message success" role="status">{notice}</div>}
    </section>
  )
}
