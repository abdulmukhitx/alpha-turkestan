import { useI18n } from '../i18n.jsx'

export default function EvidenceBadge({ evidence }) {
  const { t, formatNumber } = useI18n()
  if (!evidence) return null

  const quality = evidence.quality || {}
  const isDemo = evidence.kind === 'synthetic_demo'
  const isModel = evidence.kind === 'modeled_scenario'
  const coverage = Number(quality.valid_coverage_percent)
  const version = evidence.data_version ? evidence.data_version.slice(0, 12) : null

  return (
    <details className={`evidence-badge ${isDemo ? 'demo' : isModel ? 'model' : quality.cloud_mask_applied ? 'verified' : 'limited'}`}>
      <summary>
        <span className="evidence-status-dot" aria-hidden="true" />
        <strong>{isDemo ? t('evidence.demo') : isModel ? t('evidence.modeled') : t('evidence.observed')}</strong>
        <span>{isModel ? t('evidence.lowConfidence') : quality.cloud_mask_applied ? t('evidence.qualityVerified') : t('evidence.qualityLimited')}</span>
      </summary>
      <div className="evidence-detail">
        <dl>
          <div><dt>{t('evidence.source')}</dt><dd>{evidence.source || '—'}</dd></div>
          {evidence.product && <div><dt>{t('evidence.product')}</dt><dd>{evidence.product}</dd></div>}
          {evidence.acquisition_window && <div><dt>{t('evidence.window')}</dt><dd>{evidence.acquisition_window}</dd></div>}
          {evidence.spatial_resolution_m && <div><dt>{t('evidence.resolution')}</dt><dd>{evidence.spatial_resolution_m} m</dd></div>}
          {evidence.bands?.length > 0 && <div><dt>{t('evidence.bands')}</dt><dd>{evidence.bands.join(', ')}</dd></div>}
          {Number.isFinite(coverage) && <div><dt>{t('evidence.coverage')}</dt><dd>{formatNumber(coverage)}%</dd></div>}
          {version && <div><dt>{t('evidence.version')}</dt><dd><code>{version}</code></dd></div>}
        </dl>
        {!isDemo && !quality.cloud_mask_applied && <p>{t('evidence.maskLimitation')}</p>}
        {isModel && <p>{t('evidence.modelLimitation')}</p>}
        {evidence.provenance_completeness === 'partial' && <p>{t('evidence.provenancePartial')}</p>}
      </div>
    </details>
  )
}
