import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import ProductHeader from '../components/ProductHeader.jsx'
import { deleteSavedAnalysis, fetchCurrentAccount, fetchSavedAnalyses } from '../api.js'
import { useI18n } from '../i18n.jsx'
import { copyText } from '../share.js'

const KINDS = ['all', 'point', 'zone', 'forecast', 'change', 'transect']

function downloadAnalysis(analysis) {
  const blob = new Blob([JSON.stringify(analysis, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `geoai-analysis-${analysis.id}.json`
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

function analysisContext(analysis) {
  const payload = analysis.payload || {}
  if (analysis.kind === 'point') {
    const point = payload.point || {}
    return [payload.index?.toUpperCase(), payload.period?.slice(0, 4), point.lat && point.lng ? `${point.lat.toFixed(3)}, ${point.lng.toFixed(3)}` : null]
  }
  if (analysis.kind === 'forecast') return [payload.index?.toUpperCase(), payload.target_year]
  if (analysis.kind === 'change') return [payload.index?.toUpperCase(), `${payload.period_before?.slice(0, 4)} → ${payload.period_after?.slice(0, 4)}`]
  if (analysis.kind === 'zone') return [payload.index?.toUpperCase(), payload.period?.slice(0, 4), payload.result?.area_ha ? `${payload.result.area_ha} ha` : null]
  if (analysis.kind === 'transect') return [payload.index?.toUpperCase(), payload.period?.slice(0, 4)]
  return []
}

export default function HistoryPage() {
  const { t, formatDate } = useI18n()
  const [account, setAccount] = useState(null)
  const [analyses, setAnalyses] = useState([])
  const [kind, setKind] = useState('all')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function load() {
    setLoading(true)
    setError('')
    try {
      const session = await fetchCurrentAccount()
      setAccount(session)
      if (!session?.user?.email_verified) {
        setAnalyses([])
        return
      }
      const result = await fetchSavedAnalyses()
      setAnalyses(result.analyses || [])
    } catch (requestError) {
      setError(requestError.message || t('history.pageLoadError'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [])

  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return analyses.filter((analysis) => (
      (kind === 'all' || analysis.kind === kind)
      && (!needle || analysis.title.toLocaleLowerCase().includes(needle))
    ))
  }, [analyses, kind, query])

  async function remove(analysis) {
    if (!window.confirm(t('history.deleteConfirm'))) return
    setBusyId(analysis.id)
    setError('')
    try {
      await deleteSavedAnalysis(analysis.id)
      setAnalyses((current) => current.filter((item) => item.id !== analysis.id))
    } catch (requestError) {
      setError(requestError.message || t('error.analysisDelete'))
    } finally {
      setBusyId('')
    }
  }

  async function share(analysis) {
    const url = new URL('/map', window.location.origin)
    url.searchParams.set('analysis', analysis.id)
    await copyText(url.toString())
    setNotice(t('history.linkCopied'))
  }

  function createdAt(value) {
    const date = new Date(value)
    return Number.isNaN(date.getTime()) ? value : formatDate(date, { dateStyle: 'medium', timeStyle: 'short' })
  }

  return (
    <div className="page-shell">
      <ProductHeader account={account} />
      <main className="product-page history-page">
        <section className="page-hero">
          <div>
            <span className="page-eyebrow">{t('history.pageEyebrow')}</span>
            <h1>{t('history.pageTitle')}</h1>
            <p>{t('history.pageSubtitle')}</p>
          </div>
          <div className="page-hero-actions">
            <button type="button" className="secondary-action" onClick={load} disabled={loading}>{t('history.refresh')}</button>
            <Link className="primary-action" to="/map">{t('dashboard.openMap')}</Link>
          </div>
        </section>

        {account?.user?.email_verified && (
          <section className="history-toolbar">
            <label>{t('history.search')}<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('history.searchPlaceholder')} /></label>
            <label>{t('history.filter')}<select value={kind} onChange={(event) => setKind(event.target.value)}>{KINDS.map((item) => <option key={item} value={item}>{item === 'all' ? t('history.allKinds') : t(`history.kind.${item}`)}</option>)}</select></label>
            <strong>{t('history.resultCount', { count: visible.length })}</strong>
          </section>
        )}

        {(error || notice) && <div className={`page-message ${error ? 'error' : 'success'}`} role={error ? 'alert' : 'status'}>{error || notice}</div>}

        {loading ? (
          <div className="page-empty">{t('common.wait')}</div>
        ) : !account ? (
          <section className="page-empty"><h2>{t('history.signInTitle')}</h2><p>{t('history.signInHelp')}</p><Link className="primary-action" to="/map?account=login">{t('top.signIn')}</Link></section>
        ) : !account.user.email_verified ? (
          <section className="page-empty"><h2>{t('history.verifyTitle')}</h2><p>{t('history.verifyHelp')}</p><Link className="primary-action" to="/map?account=profile">{t('history.openProfile')}</Link></section>
        ) : visible.length === 0 ? (
          <section className="page-empty"><h2>{t('history.empty')}</h2><p>{t('history.pageEmptyHelp')}</p></section>
        ) : (
          <section className="history-card-list" aria-label={t('history.pageTitle')}>
            {visible.map((analysis) => (
              <article className="history-card" key={analysis.id}>
                <div className="history-kind-mark"><span>{t(`history.kind.${analysis.kind}`)}</span><small>{createdAt(analysis.created_at)}</small></div>
                <div className="history-card-body"><h2>{analysis.title}</h2><div>{analysisContext(analysis).filter(Boolean).map((item) => <span key={item}>{item}</span>)}</div></div>
                <div className="history-card-actions">
                  <Link className="primary-action compact" to={`/map?analysis=${encodeURIComponent(analysis.id)}`}>{t('history.open')}</Link>
                  <button type="button" onClick={() => share(analysis)}>{t('dashboard.share')}</button>
                  <button type="button" onClick={() => downloadAnalysis(analysis)}>{t('history.download')}</button>
                  <button type="button" className="danger-link" onClick={() => remove(analysis)} disabled={busyId === analysis.id}>{t('common.delete')}</button>
                </div>
              </article>
            ))}
          </section>
        )}
      </main>
    </div>
  )
}
