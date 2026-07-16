import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import ProductHeader from '../components/ProductHeader.jsx'
import {
  addFieldCaseUpdate, createFieldCase, createFieldValidation, deleteFieldCase,
  fetchAccountZones, fetchCurrentAccount, fetchFieldCase, fetchFieldCases,
  fetchGroundTruthDataset, fetchPeriods, updateFieldCase,
} from '../api.js'
import { useI18n } from '../i18n.jsx'

const EMPTY_CREATE = {
  zone_id: '', source_alert_id: null, title: '', kind: 'field_check', priority: 'normal',
  due_date: '', assignee: '', description: '',
}

const EMPTY_UPDATE = { kind: 'note', body: '' }

const KIND_OPTIONS = ['field_check', 'vegetation', 'irrigation', 'land_change', 'other']
const PRIORITY_OPTIONS = ['low', 'normal', 'high', 'urgent']
const STATUS_OPTIONS = ['open', 'in_progress', 'waiting', 'closed']
const LAND_COVER_OPTIONS = ['agriculture', 'dense_vegetation', 'sparse_vegetation', 'bare_soil', 'urban', 'water']

const EMPTY_GROUND_TRUTH = {
  samples: [],
  summary: { total: 0, comparable: 0, matches: 0, conflicts: 0, excluded: 0, agreement_percent: null },
}

function mapLayerForCase(kind) {
  if (kind === 'irrigation') return 'ndmi'
  if (kind === 'land_change') return 'nbr'
  return 'ndvi'
}

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

function localDateTimeValue(date = new Date()) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 16)
}

function geometryCenter(geometry) {
  const ring = geometry?.type === 'Polygon' ? geometry.coordinates?.[0] : null
  if (!Array.isArray(ring) || !ring.length) return null
  const points = ring.length > 1 && ring[0][0] === ring.at(-1)[0] && ring[0][1] === ring.at(-1)[1]
    ? ring.slice(0, -1) : ring
  const longitudes = points.map((point) => Number(point[0])).filter(Number.isFinite)
  const latitudes = points.map((point) => Number(point[1])).filter(Number.isFinite)
  if (!longitudes.length || !latitudes.length) return null
  return {
    longitude: (Math.min(...longitudes) + Math.max(...longitudes)) / 2,
    latitude: (Math.min(...latitudes) + Math.max(...latitudes)) / 2,
  }
}

function csvCell(value) {
  const text = value == null ? '' : String(value)
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

function downloadText(filename, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

export default function WorkPage() {
  const { t, formatDate } = useI18n()
  const [searchParams, setSearchParams] = useSearchParams()
  const [account, setAccount] = useState(null)
  const [zones, setZones] = useState([])
  const [cases, setCases] = useState([])
  const [periods, setPeriods] = useState([])
  const [groundTruth, setGroundTruth] = useState(EMPTY_GROUND_TRUTH)
  const [selectedCase, setSelectedCase] = useState(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('active')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [createForm, setCreateForm] = useState(EMPTY_CREATE)
  const [editForm, setEditForm] = useState(null)
  const [timelineForm, setTimelineForm] = useState(EMPTY_UPDATE)
  const [validationForm, setValidationForm] = useState({
    observed_class: 'agriculture', observer_confidence: 'high', period_id: '',
    observed_at: localDateTimeValue(), latitude: '', longitude: '', note: '',
  })
  const [locating, setLocating] = useState(false)

  const cloudMode = account?.user?.email_verified === true

  function displayDate(value, options = { dateStyle: 'medium' }) {
    if (!value) return t('work.noDueDate')
    const parsed = new Date(value.length === 10 ? `${value}T12:00:00` : value)
    return Number.isNaN(parsed.getTime()) ? value : formatDate(parsed, options)
  }

  function mergeCase(item) {
    setCases((current) => {
      const found = current.some((entry) => entry.id === item.id)
      return found
        ? current.map((entry) => entry.id === item.id ? { ...entry, ...item } : entry)
        : [item, ...current]
    })
  }

  async function openCase(caseId, { updateUrl = true } = {}) {
    setError('')
    try {
      const item = await fetchFieldCase(caseId)
      setSelectedCase(item)
      if (updateUrl) setSearchParams({ case: caseId }, { replace: true })
    } catch (requestError) {
      setError(requestError.message || t('work.loadError'))
    }
  }

  async function load() {
    setLoading(true)
    setError('')
    try {
      const session = await fetchCurrentAccount()
      setAccount(session)
      if (session?.user?.email_verified) {
        const [caseResult, zoneResult, periodResult, validationResult] = await Promise.all([
          fetchFieldCases(), fetchAccountZones(), fetchPeriods(), fetchGroundTruthDataset(),
        ])
        const caseItems = caseResult.cases || []
        const zoneItems = zoneResult.zones || []
        setCases(caseItems)
        setZones(zoneItems)
        setPeriods(periodResult || [])
        setGroundTruth(validationResult || EMPTY_GROUND_TRUTH)
        const requestedCase = searchParams.get('case')
        if (requestedCase) await openCase(requestedCase, { updateUrl: false })
        else if (caseItems.length) await openCase(caseItems[0].id, { updateUrl: false })

        if (searchParams.get('new') === '1') {
          const requestedZone = searchParams.get('zone') || zoneItems[0]?.id || ''
          const requestedKind = KIND_OPTIONS.includes(searchParams.get('kind'))
            ? searchParams.get('kind') : 'field_check'
          const zone = zoneItems.find((item) => item.id === requestedZone)
          setCreateForm({
            ...EMPTY_CREATE,
            zone_id: requestedZone,
            source_alert_id: searchParams.get('alert') || null,
            kind: requestedKind,
            title: zone ? t('work.defaultTitle', { zone: zone.name }) : '',
            assignee: session.user.display_name || '',
          })
          setShowCreate(true)
        }
      }
    } catch (requestError) {
      setError(requestError.message || t('work.loadError'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [])

  useEffect(() => {
    if (!selectedCase) {
      setEditForm(null)
      return
    }
    setEditForm({
      title: selectedCase.title || '',
      kind: selectedCase.kind || 'field_check',
      priority: selectedCase.priority || 'normal',
      status: selectedCase.status || 'open',
      due_date: selectedCase.due_date || '',
      assignee: selectedCase.assignee || '',
      description: selectedCase.description || '',
      finding: selectedCase.finding || '',
      action: selectedCase.action || '',
      resolution: selectedCase.resolution || '',
    })
  }, [selectedCase])

  useEffect(() => {
    if (!selectedCase) return
    const center = geometryCenter(selectedCase.zone_geometry)
    const latestPeriod = periods.filter((item) => item.available).at(-1) || periods.at(-1)
    setValidationForm({
      observed_class: 'agriculture',
      observer_confidence: 'high',
      period_id: latestPeriod?.period_id || '',
      observed_at: localDateTimeValue(),
      latitude: center ? center.latitude.toFixed(6) : '',
      longitude: center ? center.longitude.toFixed(6) : '',
      note: '',
    })
  }, [selectedCase?.id, periods])

  const filteredCases = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return cases.filter((item) => {
      const statusMatches = statusFilter === 'all'
        || (statusFilter === 'active' && item.status !== 'closed')
        || item.status === statusFilter
      const textMatches = !needle
        || `${item.title} ${item.zone_name} ${item.assignee}`.toLocaleLowerCase().includes(needle)
      return statusMatches && textMatches
    })
  }, [cases, query, statusFilter])

  const metrics = useMemo(() => ({
    active: cases.filter((item) => item.status !== 'closed').length,
    inProgress: cases.filter((item) => item.status === 'in_progress').length,
    overdue: cases.filter((item) => item.status !== 'closed' && item.due_date && item.due_date < todayIso()).length,
    closed: cases.filter((item) => item.status === 'closed').length,
  }), [cases])

  function startCreate(zoneId = '') {
    const zone = zones.find((item) => item.id === zoneId)
    setCreateForm({
      ...EMPTY_CREATE,
      zone_id: zoneId || zones[0]?.id || '',
      title: zone ? t('work.defaultTitle', { zone: zone.name }) : '',
      assignee: account?.user?.display_name || '',
    })
    setShowCreate(true)
  }

  async function submitCreate(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const created = await createFieldCase({
        ...createForm,
        due_date: createForm.due_date || null,
        source_alert_id: createForm.source_alert_id || null,
      })
      mergeCase(created)
      setSelectedCase(created)
      setSearchParams({ case: created.id }, { replace: true })
      setShowCreate(false)
      setNotice(t('work.created'))
    } catch (requestError) {
      setError(requestError.message || t('work.createError'))
    } finally {
      setBusy(false)
    }
  }

  async function saveCase(event) {
    event.preventDefault()
    if (!selectedCase || !editForm) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const updated = await updateFieldCase(selectedCase.id, {
        ...editForm,
        due_date: editForm.due_date || null,
      })
      setSelectedCase(updated)
      mergeCase(updated)
      setNotice(t('work.saved'))
    } catch (requestError) {
      setError(requestError.message || t('work.saveError'))
    } finally {
      setBusy(false)
    }
  }

  async function addTimelineUpdate(event) {
    event.preventDefault()
    if (!selectedCase || !timelineForm.body.trim()) return
    setBusy(true)
    setError('')
    try {
      const created = await addFieldCaseUpdate(selectedCase.id, timelineForm)
      setSelectedCase((current) => ({
        ...current,
        updated_at: created.created_at,
        update_count: (current.update_count || 0) + 1,
        updates: [created, ...(current.updates || [])],
      }))
      setCases((current) => current.map((item) => item.id === selectedCase.id
        ? { ...item, updated_at: created.created_at, update_count: (item.update_count || 0) + 1 }
        : item))
      setTimelineForm(EMPTY_UPDATE)
      setNotice(t('work.updateAdded'))
    } catch (requestError) {
      setError(requestError.message || t('work.updateError'))
    } finally {
      setBusy(false)
    }
  }

  async function addValidation(event) {
    event.preventDefault()
    if (!selectedCase) return
    const latitude = Number(validationForm.latitude)
    const longitude = Number(validationForm.longitude)
    const observedDate = new Date(validationForm.observed_at)
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || Number.isNaN(observedDate.getTime())) {
      setError(t('work.validation.invalidLocation'))
      return
    }
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await createFieldValidation(selectedCase.id, {
        latitude,
        longitude,
        observed_at: observedDate.toISOString(),
        period_id: validationForm.period_id,
        observed_class: validationForm.observed_class,
        observer_confidence: validationForm.observer_confidence,
        note: validationForm.note,
      })
      const created = result.update
      setSelectedCase((current) => ({
        ...current,
        updated_at: created.created_at,
        update_count: (current.update_count || 0) + 1,
        updates: [created, ...(current.updates || [])],
      }))
      setCases((current) => current.map((item) => item.id === selectedCase.id
        ? { ...item, updated_at: created.created_at, update_count: (item.update_count || 0) + 1 }
        : item))
      setGroundTruth((current) => ({
        summary: result.summary,
        samples: result.sample
          ? [result.sample, ...current.samples.filter((sample) => sample.id !== result.sample.id)]
          : current.samples,
      }))
      setValidationForm((current) => ({ ...current, note: '' }))
      setNotice(t(`work.validation.saved.${result.sample?.comparison || 'unclassified'}`))
    } catch (requestError) {
      setError(requestError.message || t('work.validation.saveError'))
    } finally {
      setBusy(false)
    }
  }

  function useCurrentLocation() {
    if (!navigator.geolocation) {
      setError(t('work.validation.locationUnavailable'))
      return
    }
    setLocating(true)
    setError('')
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        setValidationForm((current) => ({
          ...current,
          latitude: coords.latitude.toFixed(6),
          longitude: coords.longitude.toFixed(6),
        }))
        setLocating(false)
      },
      () => {
        setError(t('work.validation.locationDenied'))
        setLocating(false)
      },
      { enableHighAccuracy: true, timeout: 12_000, maximumAge: 30_000 },
    )
  }

  function exportGroundTruth(format) {
    if (!groundTruth.samples.length) return
    const filename = `geoai-ground-truth-${todayIso()}`
    if (format === 'geojson') {
      const collection = {
        type: 'FeatureCollection',
        name: 'GeoAI TKO ground truth',
        features: groundTruth.samples.map((sample) => ({
          type: 'Feature',
          id: sample.id,
          geometry: { type: 'Point', coordinates: [sample.longitude, sample.latitude] },
          properties: {
            case_id: sample.case_id, case_title: sample.case_title,
            zone_id: sample.zone_id, zone_name: sample.zone_name, assignee: sample.assignee,
            observed_at: sample.observed_at, observed_class: sample.observed_class,
            observer_confidence: sample.observer_confidence, comparison: sample.comparison,
            satellite_period: sample.satellite_period, satellite_class: sample.satellite_class,
            satellite_confidence: sample.satellite_confidence, data_version: sample.data_version,
            ...sample.indices,
          },
        })),
      }
      downloadText(`${filename}.geojson`, JSON.stringify(collection, null, 2), 'application/geo+json;charset=utf-8')
      return
    }
    const fields = [
      'id', 'case_id', 'case_title', 'zone_id', 'zone_name', 'assignee', 'latitude', 'longitude',
      'observed_at', 'observed_class', 'observer_confidence', 'comparison', 'satellite_period',
      'satellite_class', 'satellite_confidence', 'data_version', 'ndvi', 'ndwi', 'ndre', 'ndmi',
      'bsi', 'savi', 'nbr', 'note',
    ]
    const rows = groundTruth.samples.map((sample) => fields.map((field) => (
      csvCell(Object.hasOwn(sample.indices || {}, field) ? sample.indices[field] : sample[field])
    )).join(','))
    downloadText(`${filename}.csv`, `\uFEFF${fields.join(',')}\r\n${rows.join('\r\n')}`, 'text/csv;charset=utf-8')
  }

  async function removeCase() {
    if (!selectedCase || !window.confirm(t('work.deleteConfirm'))) return
    setBusy(true)
    setError('')
    try {
      await deleteFieldCase(selectedCase.id)
      setGroundTruth(await fetchGroundTruthDataset())
      const remaining = cases.filter((item) => item.id !== selectedCase.id)
      setCases(remaining)
      setSelectedCase(null)
      setSearchParams({}, { replace: true })
      if (remaining.length) await openCase(remaining[0].id)
      setNotice(t('work.deleted'))
    } catch (requestError) {
      setError(requestError.message || t('work.deleteError'))
    } finally {
      setBusy(false)
    }
  }

  function renderTimelineBody(update) {
    if (update.kind !== 'status') return update.body
    const [from, to] = update.body.split(' -> ')
    return t('work.statusChanged', {
      from: t(`work.status.${from}`), to: t(`work.status.${to}`),
    })
  }

  function renderValidationResult(update) {
    const validation = update.evidence?.ground_truth
    if (!validation) return null
    const satellite = validation.satellite || {}
    return (
      <div className={`timeline-validation ${validation.comparison}`}>
        <div>
          <span>{t('work.validation.ground')}</span>
          <strong>{t(`lulc.${validation.observed_class}`)}</strong>
        </div>
        <span aria-hidden="true">↔</span>
        <div>
          <span>{t('work.validation.satellite')}</span>
          <strong>{satellite.class ? t(`lulc.${satellite.class}`) : t('work.validation.noModel')}</strong>
        </div>
        <small>{t(`work.validation.result.${validation.comparison}`)} · {satellite.period_id}</small>
      </div>
    )
  }

  const selectedMapUrl = selectedCase
    ? `/map?${new URLSearchParams({
      zone: selectedCase.zone_id,
      layer: selectedCase.source_alert?.index || mapLayerForCase(selectedCase.kind),
      ...(selectedCase.source_alert?.period_id ? { period: selectedCase.source_alert.period_id } : {}),
    })}`
    : '/map'

  const validationPeriodMismatch = validationForm.period_id.slice(0, 4)
    && validationForm.observed_at.slice(0, 4)
    && validationForm.period_id.slice(0, 4) !== validationForm.observed_at.slice(0, 4)

  return (
    <div className="page-shell">
      <ProductHeader account={account} />
      <main className="product-page work-page">
        <section className="page-hero work-hero">
          <div>
            <span className="page-eyebrow">FIELD OPERATIONS</span>
            <h1>{t('work.title')}</h1>
            <p>{t('work.subtitle')}</p>
          </div>
          {cloudMode && <button type="button" className="primary-action" onClick={() => startCreate()} disabled={!zones.length}>{t('work.newCase')}</button>}
        </section>

        {(error || notice) && <div className={`page-message ${error ? 'error' : 'success'}`} role={error ? 'alert' : 'status'}>{error || notice}</div>}

        {loading ? (
          <div className="page-empty">{t('common.wait')}</div>
        ) : !account ? (
          <section className="page-empty">
            <h2>{t('work.signInTitle')}</h2><p>{t('work.signInHelp')}</p>
            <Link className="primary-action" to="/map?account=login">{t('top.signIn')}</Link>
          </section>
        ) : !cloudMode ? (
          <section className="page-empty">
            <h2>{t('work.verifyTitle')}</h2><p>{t('work.verifyHelp')}</p>
            <Link className="primary-action" to="/map?account=profile">{t('history.openProfile')}</Link>
          </section>
        ) : !zones.length ? (
          <section className="page-empty">
            <h2>{t('work.noZonesTitle')}</h2><p>{t('work.noZonesHelp')}</p>
            <Link className="primary-action" to="/map">{t('dashboard.openMap')}</Link>
          </section>
        ) : (
          <>
            <section className="metric-grid work-metrics" aria-label={t('work.summary')}>
              <article><span>{t('work.metric.active')}</span><strong>{metrics.active}</strong><small>{t('work.metric.activeHint')}</small></article>
              <article><span>{t('work.metric.inProgress')}</span><strong>{metrics.inProgress}</strong><small>{t('work.metric.inProgressHint')}</small></article>
              <article className={metrics.overdue ? 'metric-warning' : ''}><span>{t('work.metric.overdue')}</span><strong>{metrics.overdue}</strong><small>{t('work.metric.overdueHint')}</small></article>
              <article><span>{t('work.metric.closed')}</span><strong>{metrics.closed}</strong><small>{t('work.metric.closedHint')}</small></article>
            </section>

            <section className="ground-truth-overview" aria-labelledby="ground-truth-title">
              <div className="ground-truth-intro">
                <span className="page-eyebrow">VALIDATION DATASET</span>
                <h2 id="ground-truth-title">{t('work.validation.datasetTitle')}</h2>
                <p>{t('work.validation.datasetHelp')}</p>
              </div>
              <div className="ground-truth-stats">
                <article><strong>{groundTruth.summary.total}</strong><span>{t('work.validation.samples')}</span></article>
                <article><strong>{groundTruth.summary.comparable}</strong><span>{t('work.validation.comparable')}</span></article>
                <article className={groundTruth.summary.conflicts ? 'has-conflicts' : ''}><strong>{groundTruth.summary.conflicts}</strong><span>{t('work.validation.conflicts')}</span></article>
                <article><strong>{groundTruth.summary.agreement_percent == null ? '—' : `${groundTruth.summary.agreement_percent}%`}</strong><span>{t('work.validation.agreement')}</span></article>
              </div>
              <div className="ground-truth-actions">
                <button type="button" onClick={() => exportGroundTruth('csv')} disabled={!groundTruth.samples.length}>{t('work.validation.exportCsv')}</button>
                <button type="button" onClick={() => exportGroundTruth('geojson')} disabled={!groundTruth.samples.length}>{t('work.validation.exportGeojson')}</button>
              </div>
            </section>

            <section className="work-toolbar">
              <label>
                <span>{t('work.search')}</span>
                <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('work.searchPlaceholder')} />
              </label>
              <label>
                <span>{t('work.filter')}</span>
                <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                  <option value="active">{t('work.filterActive')}</option>
                  <option value="all">{t('work.filterAll')}</option>
                  {STATUS_OPTIONS.map((status) => <option value={status} key={status}>{t(`work.status.${status}`)}</option>)}
                </select>
              </label>
              <strong>{t('work.resultCount', { count: filteredCases.length })}</strong>
            </section>

            {!cases.length ? (
              <section className="page-empty">
                <h2>{t('work.emptyTitle')}</h2><p>{t('work.emptyHelp')}</p>
                <button type="button" className="primary-action" onClick={() => startCreate()}>{t('work.newCase')}</button>
              </section>
            ) : (
              <div className="work-layout">
                <section className="case-list" aria-label={t('work.caseList')}>
                  {!filteredCases.length && <div className="case-list-empty">{t('work.noResults')}</div>}
                  {filteredCases.map((item) => {
                    const overdue = item.status !== 'closed' && item.due_date && item.due_date < todayIso()
                    return (
                      <button type="button" className={`case-card ${selectedCase?.id === item.id ? 'selected' : ''}`} key={item.id} onClick={() => openCase(item.id)}>
                        <span className={`case-priority ${item.priority}`} aria-hidden="true" />
                        <span className="case-card-body">
                          <span className="case-card-topline">
                            <strong>{item.title}</strong>
                            <small className={`case-status ${item.status}`}>{t(`work.status.${item.status}`)}</small>
                          </span>
                          <span>{item.zone_name} · {t(`work.kind.${item.kind}`)}</span>
                          <small className={overdue ? 'overdue' : ''}>{item.due_date ? `${t('work.due')} ${displayDate(item.due_date)}` : t('work.noDueDate')} · {t('work.updates', { count: item.update_count || 0 })}</small>
                        </span>
                      </button>
                    )
                  })}
                </section>

                {selectedCase && editForm ? (
                  <article className="case-workbench">
                    <header className="case-workbench-header">
                      <div>
                        <span className="page-eyebrow">{t(`work.kind.${selectedCase.kind}`)}</span>
                        <h2>{selectedCase.title}</h2>
                        <p>{selectedCase.zone_name} · {t('work.createdAt')} {displayDate(selectedCase.created_at)}</p>
                      </div>
                      <div className="case-workbench-actions">
                        <Link to={selectedMapUrl}>{t('work.openEvidence')}</Link>
                        <button type="button" onClick={() => window.print()}>{t('work.print')}</button>
                      </div>
                    </header>

                    {selectedCase.source_alert && (
                      <section className="case-trigger" aria-label={t('work.trigger')}>
                        <span aria-hidden="true">!</span>
                        <div>
                          <small>{t('work.trigger')}</small>
                          <strong>
                            {selectedCase.source_alert.index.toUpperCase()} {selectedCase.source_alert.operator === 'below' ? '<' : '>'} {Number(selectedCase.source_alert.threshold).toFixed(2)}
                          </strong>
                          <p>{t('work.triggerObserved', { value: Number(selectedCase.source_alert.observed_value).toFixed(3) })}</p>
                        </div>
                        <time>{selectedCase.source_alert.period_id} · {displayDate(selectedCase.source_alert.last_observed_at)}</time>
                      </section>
                    )}

                    <section className="case-validation" aria-labelledby="case-validation-title">
                      <div className="case-section-heading">
                        <div><span className="page-eyebrow">GROUND TRUTH</span><h3 id="case-validation-title">{t('work.validation.title')}</h3></div>
                        <small>{t('work.validation.caseSamples', { count: groundTruth.samples.filter((sample) => sample.case_id === selectedCase.id).length })}</small>
                      </div>
                      <p className="case-validation-help">{t('work.validation.help')}</p>
                      <form className="validation-form" onSubmit={addValidation}>
                        <label><span>{t('work.validation.observedClass')}</span><select value={validationForm.observed_class} onChange={(event) => setValidationForm({ ...validationForm, observed_class: event.target.value })}>{LAND_COVER_OPTIONS.map((item) => <option value={item} key={item}>{t(`lulc.${item}`)}</option>)}</select></label>
                        <label><span>{t('work.validation.confidence')}</span><select value={validationForm.observer_confidence} onChange={(event) => setValidationForm({ ...validationForm, observer_confidence: event.target.value })}>{['high', 'medium', 'low'].map((item) => <option value={item} key={item}>{t(`work.validation.confidence.${item}`)}</option>)}</select></label>
                        <label><span>{t('work.validation.period')}</span><select required value={validationForm.period_id} onChange={(event) => setValidationForm({ ...validationForm, period_id: event.target.value })}>{periods.map((item) => <option value={item.period_id} key={item.period_id} disabled={!item.available}>{item.label || item.period_id}{!item.available ? ` · ${t('work.validation.unavailable')}` : ''}</option>)}</select></label>
                        <label><span>{t('work.validation.observedAt')}</span><input required type="datetime-local" value={validationForm.observed_at} onChange={(event) => setValidationForm({ ...validationForm, observed_at: event.target.value })} /></label>
                        <label><span>{t('work.validation.latitude')}</span><input required inputMode="decimal" value={validationForm.latitude} onChange={(event) => setValidationForm({ ...validationForm, latitude: event.target.value })} /></label>
                        <label><span>{t('work.validation.longitude')}</span><input required inputMode="decimal" value={validationForm.longitude} onChange={(event) => setValidationForm({ ...validationForm, longitude: event.target.value })} /></label>
                        <label className="wide"><span>{t('work.validation.note')}</span><textarea rows="2" value={validationForm.note} onChange={(event) => setValidationForm({ ...validationForm, note: event.target.value })} placeholder={t('work.validation.notePlaceholder')} /></label>
                        <div className="validation-form-footer wide">
                          <button type="button" className="location-action" onClick={useCurrentLocation} disabled={locating}>{locating ? t('common.wait') : t('work.validation.useLocation')}</button>
                          <div>
                            {validationPeriodMismatch && <small className="validation-warning">{t('work.validation.periodWarning')}</small>}
                            <button type="submit" className="primary-action compact" disabled={busy || !validationForm.period_id}>{busy ? t('common.wait') : t('work.validation.compare')}</button>
                          </div>
                        </div>
                      </form>
                    </section>

                    <form className="case-editor" onSubmit={saveCase}>
                      <div className="case-editor-grid">
                        <label className="wide"><span>{t('work.field.title')}</span><input required value={editForm.title} onChange={(event) => setEditForm({ ...editForm, title: event.target.value })} /></label>
                        <label><span>{t('work.field.kind')}</span><select value={editForm.kind} onChange={(event) => setEditForm({ ...editForm, kind: event.target.value })}>{KIND_OPTIONS.map((kind) => <option value={kind} key={kind}>{t(`work.kind.${kind}`)}</option>)}</select></label>
                        <label><span>{t('work.field.status')}</span><select value={editForm.status} onChange={(event) => setEditForm({ ...editForm, status: event.target.value })}>{STATUS_OPTIONS.map((status) => <option value={status} key={status}>{t(`work.status.${status}`)}</option>)}</select></label>
                        <label><span>{t('work.field.priority')}</span><select value={editForm.priority} onChange={(event) => setEditForm({ ...editForm, priority: event.target.value })}>{PRIORITY_OPTIONS.map((priority) => <option value={priority} key={priority}>{t(`work.priority.${priority}`)}</option>)}</select></label>
                        <label><span>{t('work.field.dueDate')}</span><input type="date" value={editForm.due_date} onChange={(event) => setEditForm({ ...editForm, due_date: event.target.value })} /></label>
                        <label className="wide"><span>{t('work.field.assignee')}</span><input value={editForm.assignee} onChange={(event) => setEditForm({ ...editForm, assignee: event.target.value })} /></label>
                        <label className="wide"><span>{t('work.field.description')}</span><textarea rows="3" value={editForm.description} onChange={(event) => setEditForm({ ...editForm, description: event.target.value })} placeholder={t('work.placeholder.description')} /></label>
                        <label className="wide"><span>{t('work.field.finding')}</span><textarea rows="3" value={editForm.finding} onChange={(event) => setEditForm({ ...editForm, finding: event.target.value })} placeholder={t('work.placeholder.finding')} /></label>
                        <label className="wide"><span>{t('work.field.action')}</span><textarea rows="3" value={editForm.action} onChange={(event) => setEditForm({ ...editForm, action: event.target.value })} placeholder={t('work.placeholder.action')} /></label>
                        <label className="wide"><span>{t('work.field.resolution')}</span><textarea rows="3" value={editForm.resolution} onChange={(event) => setEditForm({ ...editForm, resolution: event.target.value })} placeholder={t('work.placeholder.resolution')} /></label>
                      </div>
                      <div className="case-editor-actions">
                        <button className="primary-action compact" type="submit" disabled={busy}>{busy ? t('common.wait') : t('common.save')}</button>
                        <button className="danger-text-action" type="button" onClick={removeCase} disabled={busy}>{t('common.delete')}</button>
                      </div>
                      {editForm.status === 'closed' && !editForm.resolution.trim() && <small className="case-close-hint">{t('work.resolutionRequired')}</small>}
                    </form>

                    <section className="case-timeline">
                      <div className="case-section-heading"><div><span className="page-eyebrow">LOGBOOK</span><h3>{t('work.timeline')}</h3></div><small>{t('work.updates', { count: selectedCase.updates?.length || 0 })}</small></div>
                      <form className="timeline-composer" onSubmit={addTimelineUpdate}>
                        <select value={timelineForm.kind} onChange={(event) => setTimelineForm({ ...timelineForm, kind: event.target.value })} aria-label={t('work.updateType')}>
                          {['note', 'field_observation', 'action', 'decision'].map((kind) => <option value={kind} key={kind}>{t(`work.updateKind.${kind}`)}</option>)}
                        </select>
                        <textarea rows="2" required value={timelineForm.body} onChange={(event) => setTimelineForm({ ...timelineForm, body: event.target.value })} placeholder={t('work.updatePlaceholder')} />
                        <button type="submit" disabled={busy || !timelineForm.body.trim()}>{t('work.addUpdate')}</button>
                      </form>
                      <div className="timeline-list">
                        {(selectedCase.updates || []).map((update) => (
                          <article className={`timeline-entry ${update.kind}`} key={update.id}>
                            <span aria-hidden="true" />
                            <div>
                              <div><strong>{t(`work.updateKind.${update.kind}`)}</strong><time>{displayDate(update.created_at, { dateStyle: 'medium', timeStyle: 'short' })}</time></div>
                              <p>{renderTimelineBody(update)}</p>
                              {renderValidationResult(update)}
                              {Number.isFinite(update.latitude) && Number.isFinite(update.longitude) && <small>{update.latitude.toFixed(5)}, {update.longitude.toFixed(5)}</small>}
                            </div>
                          </article>
                        ))}
                      </div>
                    </section>
                  </article>
                ) : <section className="case-workbench case-workbench-empty"><h2>{t('work.selectTitle')}</h2><p>{t('work.selectHelp')}</p></section>}
              </div>
            )}
          </>
        )}
      </main>

      {showCreate && (
        <div className="work-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowCreate(false) }}>
          <section className="work-dialog" role="dialog" aria-modal="true" aria-labelledby="new-case-title">
            <header><div><span className="page-eyebrow">NEW WORK ITEM</span><h2 id="new-case-title">{t('work.createTitle')}</h2><p>{t('work.createHelp')}</p></div><button type="button" onClick={() => setShowCreate(false)} aria-label={t('common.close')}>×</button></header>
            <form onSubmit={submitCreate}>
              <label><span>{t('work.field.zone')}</span><select required value={createForm.zone_id} onChange={(event) => {
                const zone = zones.find((item) => item.id === event.target.value)
                setCreateForm({ ...createForm, zone_id: event.target.value, title: createForm.title || (zone ? t('work.defaultTitle', { zone: zone.name }) : '') })
              }}>{zones.map((zone) => <option value={zone.id} key={zone.id}>{zone.name}</option>)}</select></label>
              <label><span>{t('work.field.title')}</span><input required value={createForm.title} onChange={(event) => setCreateForm({ ...createForm, title: event.target.value })} /></label>
              <div className="work-dialog-row">
                <label><span>{t('work.field.kind')}</span><select value={createForm.kind} onChange={(event) => setCreateForm({ ...createForm, kind: event.target.value })}>{KIND_OPTIONS.map((kind) => <option value={kind} key={kind}>{t(`work.kind.${kind}`)}</option>)}</select></label>
                <label><span>{t('work.field.priority')}</span><select value={createForm.priority} onChange={(event) => setCreateForm({ ...createForm, priority: event.target.value })}>{PRIORITY_OPTIONS.map((priority) => <option value={priority} key={priority}>{t(`work.priority.${priority}`)}</option>)}</select></label>
              </div>
              <div className="work-dialog-row">
                <label><span>{t('work.field.dueDate')}</span><input type="date" value={createForm.due_date} onChange={(event) => setCreateForm({ ...createForm, due_date: event.target.value })} /></label>
                <label><span>{t('work.field.assignee')}</span><input value={createForm.assignee} onChange={(event) => setCreateForm({ ...createForm, assignee: event.target.value })} /></label>
              </div>
              <label><span>{t('work.field.description')}</span><textarea rows="4" value={createForm.description} onChange={(event) => setCreateForm({ ...createForm, description: event.target.value })} placeholder={t('work.placeholder.description')} /></label>
              <footer><button type="button" className="secondary-action" onClick={() => setShowCreate(false)}>{t('common.cancel')}</button><button type="submit" className="primary-action" disabled={busy || !createForm.zone_id || !createForm.title.trim()}>{busy ? t('common.wait') : t('work.create')}</button></footer>
            </form>
          </section>
        </div>
      )}
    </div>
  )
}
