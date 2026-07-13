import { useI18n } from '../i18n.jsx'

export default function ZoneMigrationDialog({ zones, loading, error, onImport, onSkip }) {
  const { t } = useI18n()
  if (!zones?.length) return null
  return (
    <div className="account-overlay">
      <section className="account-dialog migration-dialog" role="dialog" aria-modal="true" aria-labelledby="migration-title">
        <div className="migration-icon" aria-hidden="true">↥</div>
        <span className="account-eyebrow">{t('migration.eyebrow')}</span>
        <h2 id="migration-title">{t('migration.title')}</h2>
        <p>{t('migration.body', { count: zones.length })}</p>
        {error && <div className="account-message error" role="alert">{error}</div>}
        <div className="account-primary-actions">
          <button type="button" className="account-primary" onClick={onImport} disabled={loading}>{loading ? t('migration.importing') : t('migration.import')}</button>
          <button type="button" className="account-secondary" onClick={onSkip} disabled={loading}>{t('migration.skip')}</button>
        </div>
        <small>{t('migration.note')}</small>
      </section>
    </div>
  )
}
