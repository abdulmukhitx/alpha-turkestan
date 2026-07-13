import { useState } from 'react'
import { useI18n } from '../i18n.jsx'

export default function SaveAnalysisAction({ visible, onSave }) {
  const { t } = useI18n()
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  if (!visible) return null

  async function save() {
    setBusy(true)
    setSaved(false)
    setError('')
    try {
      await onSave()
      setSaved(true)
    } catch (requestError) {
      setError(requestError?.code ? t(requestError.code) : (requestError.message || t('error.analysisSave')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="analysis-save-action">
      <button type="button" onClick={save} disabled={busy || saved}>
        {busy ? t('common.wait') : saved ? `✓ ${t('history.saved')}` : t('history.save')}
      </button>
      {error && <span role="alert">{error}</span>}
    </div>
  )
}
