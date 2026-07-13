import { useState } from 'react'
import { useI18n } from '../i18n.jsx'

function formatUpdated(value, formatDate) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return formatDate(date, { day: '2-digit', month: '2-digit', year: '2-digit' })
}

export default function SavedZones({
  zones, activeZoneId, hasZone, dirty, editMode, storageError,
  cloudMode = false,
  onSave, onUpdate, onOpen, onRename, onDelete, onToggleEdit,
}) {
  const { t, formatDate } = useI18n()
  const [newName, setNewName] = useState('')
  const [renamingId, setRenamingId] = useState(null)
  const [renameValue, setRenameValue] = useState('')
  const activeZone = zones.find((zone) => zone.id === activeZoneId)

  async function submitNew(event) {
    event.preventDefault()
    const value = newName.trim()
    if (!value) return
    const saved = await onSave(value)
    if (saved !== false) setNewName('')
  }

  function beginRename(zone) {
    setRenamingId(zone.id)
    setRenameValue(zone.name)
  }

  async function submitRename(event, id) {
    event.preventDefault()
    const value = renameValue.trim()
    if (!value) return
    const renamed = await onRename(id, value)
    if (renamed !== false) setRenamingId(null)
  }

  return (
    <section className="panel-section saved-zones" aria-labelledby="saved-zones-heading">
      <div className="section-heading" id="saved-zones-heading">
        <span>{t('saved.title')}</span>
        <span className="saved-zone-heading-meta">
          {cloudMode && <span className="cloud-save-badge">{t('common.cloud')}</span>}
          <span className="section-count">{zones.length}</span>
        </span>
      </div>

      {hasZone && (
        <div className="zone-current-card">
          <div>
            <strong>{activeZone?.name || t('saved.newZone')}</strong>
            <span>{activeZone ? (dirty ? t('saved.unsaved') : t('saved.saved')) : t('saved.namePrompt')}</span>
          </div>
          <button
            type="button"
            className={editMode ? 'active' : ''}
            onClick={onToggleEdit}
            aria-pressed={editMode}
          >
            {editMode ? t('common.done') : t('common.edit')}
          </button>
        </div>
      )}

      {hasZone && !activeZone && (
        <form className="zone-save-form" onSubmit={submitNew}>
          <label className="sr-only" htmlFor="new-zone-name">{t('saved.nameLabel')}</label>
          <input
            id="new-zone-name"
            value={newName}
            maxLength={80}
            onChange={(event) => setNewName(event.target.value)}
            placeholder={t('saved.namePlaceholder')}
          />
          <button type="submit" disabled={!newName.trim()}>{t('common.save')}</button>
        </form>
      )}

      {activeZone && dirty && (
        <button type="button" className="zone-update-button" onClick={onUpdate}>
          {t('saved.saveChanges')}
        </button>
      )}

      {storageError && <div className="zone-error zone-storage-error">{storageError}</div>}

      {zones.length === 0 ? (
        <div className="saved-zones-empty">
          {cloudMode
            ? t('saved.cloudEmpty')
            : t('saved.localEmpty')}
        </div>
      ) : (
        <div className="saved-zone-list">
          {zones.map((zone) => (
            <div className={`saved-zone-row ${zone.id === activeZoneId ? 'active' : ''}`} key={zone.id}>
              {renamingId === zone.id ? (
                <form className="saved-zone-rename" onSubmit={(event) => submitRename(event, zone.id)}>
                  <input
                    value={renameValue}
                    maxLength={80}
                    autoFocus
                    onChange={(event) => setRenameValue(event.target.value)}
                    aria-label={t('saved.newName')}
                  />
                  <button type="submit" disabled={!renameValue.trim()}>✓</button>
                  <button type="button" onClick={() => setRenamingId(null)}>×</button>
                </form>
              ) : (
                <>
                  <button type="button" className="saved-zone-open" onClick={() => onOpen(zone.id)}>
                    <span>{zone.name}</span>
                    <small>{formatUpdated(zone.updatedAt || zone.createdAt, formatDate)}</small>
                  </button>
                  <div className="saved-zone-actions">
                    <button type="button" onClick={() => beginRename(zone)} title={t('common.rename')} aria-label={t('saved.renameAria', { name: zone.name })}>✎</button>
                    <button type="button" onClick={() => onDelete(zone.id)} title={t('common.delete')} aria-label={t('saved.deleteAria', { name: zone.name })}>×</button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
