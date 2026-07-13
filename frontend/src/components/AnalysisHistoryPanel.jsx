import { useEffect, useState } from 'react'
import { useI18n } from '../i18n.jsx'

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

export default function AnalysisHistoryPanel({ refreshKey, onFetch, onDelete }) {
  const { t, formatDate } = useI18n()
  const [analyses, setAnalyses] = useState([])
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState('')
  const [error, setError] = useState('')

  async function load() {
    setLoading(true)
    setError('')
    try {
      const result = await onFetch()
      setAnalyses(result.analyses || [])
    } catch (requestError) {
      setError(requestError?.code ? t(requestError.code) : (requestError.message || t('error.analysisHistory')))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // The profile dialog owns these callbacks while this panel is mounted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey])

  async function remove(analysis) {
    if (!window.confirm(t('history.deleteConfirm'))) return
    setBusyId(analysis.id)
    setError('')
    try {
      await onDelete(analysis.id)
      setAnalyses((current) => current.filter((item) => item.id !== analysis.id))
    } catch (requestError) {
      setError(requestError?.code ? t(requestError.code) : (requestError.message || t('error.analysisDelete')))
    } finally {
      setBusyId('')
    }
  }

  function createdAt(value) {
    const date = new Date(value)
    return Number.isNaN(date.getTime())
      ? value
      : formatDate(date, { dateStyle: 'medium', timeStyle: 'short' })
  }

  return (
    <section className="analysis-history" aria-labelledby="analysis-history-title">
      <div className="analysis-history-heading">
        <div>
          <h3 id="analysis-history-title">{t('history.title')}</h3>
          <p>{t('history.help')}</p>
        </div>
        <button type="button" onClick={load} disabled={loading}>{t('history.refresh')}</button>
      </div>

      {loading ? (
        <p className="analysis-history-empty">{t('common.wait')}</p>
      ) : analyses.length === 0 ? (
        <p className="analysis-history-empty">{t('history.empty')}</p>
      ) : (
        <ul className="analysis-history-list">
          {analyses.map((analysis) => (
            <li key={analysis.id}>
              <div>
                <span>{t(`history.kind.${analysis.kind}`)}</span>
                <strong>{analysis.title}</strong>
                <small>{createdAt(analysis.created_at)}</small>
              </div>
              <div className="analysis-history-actions">
                <button type="button" onClick={() => downloadAnalysis(analysis)}>{t('history.download')}</button>
                <button type="button" className="danger-link" onClick={() => remove(analysis)} disabled={busyId === analysis.id}>{t('common.delete')}</button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {error && <div className="account-message error" role="alert">{error}</div>}
    </section>
  )
}
