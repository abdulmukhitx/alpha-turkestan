import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import ProductHeader from '../components/ProductHeader.jsx'
import {
  addFieldCaseUpdate, createFieldCase, deleteFieldCase, fetchAccountZones,
  fetchCurrentAccount, fetchFieldCase, fetchFieldCases, updateFieldCase,
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

function mapLayerForCase(kind) {
  if (kind === 'irrigation') return 'ndmi'
  if (kind === 'land_change') return 'nbr'
  return 'ndvi'
}

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

export default function WorkPage() {
  const { t, formatDate } = useI18n()
  const [searchParams, setSearchParams] = useSearchParams()
  const [account, setAccount] = useState(null)
  const [zones, setZones] = useState([])
  const [cases, setCases] = useState([])
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
        const [caseResult, zoneResult] = await Promise.all([fetchFieldCases(), fetchAccountZones()])
        const caseItems = caseResult.cases || []
        const zoneItems = zoneResult.zones || []
        setCases(caseItems)
        setZones(zoneItems)
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

  async function removeCase() {
    if (!selectedCase || !window.confirm(t('work.deleteConfirm'))) return
    setBusy(true)
    setError('')
    try {
      await deleteFieldCase(selectedCase.id)
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

  const selectedMapUrl = selectedCase
    ? `/map?${new URLSearchParams({
      zone: selectedCase.zone_id,
      layer: selectedCase.source_alert?.index || mapLayerForCase(selectedCase.kind),
      ...(selectedCase.source_alert?.period_id ? { period: selectedCase.source_alert.period_id } : {}),
    })}`
    : '/map'

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
