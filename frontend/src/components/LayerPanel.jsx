import SavedZones from './SavedZones.jsx'
import { useI18n } from '../i18n.jsx'

const SWATCH = {
  satellite: 'linear-gradient(90deg,#1b3a2b,#3a7a4f,#8fbf6f)',
  ndvi: 'linear-gradient(90deg,#a50026,#fdae61,#ffffbf,#a6d96a,#1a9850)',
  ndwi: 'linear-gradient(90deg,#b2182b,#f7f7f7,#2166ac)',
  ndre: 'linear-gradient(90deg,#d73027,#fee08b,#1a9850)',
  ndmi: 'linear-gradient(90deg,#67001f,#f4a582,#f7f7f7,#92c5de,#053061)',
  bsi:  'linear-gradient(90deg,#fff5eb,#fdd0a2,#fd8d3c,#d94801,#7f2704)',
  savi: 'linear-gradient(90deg,#a50026,#fdae61,#ffffbf,#a6d96a,#1a9850)',
  nbr:  'linear-gradient(90deg,#a50026,#fdae61,#ffffbf,#a6d96a,#1a9850)',
}

const NAMES = {
  satellite: ['SAT', 'layer.satellite'],
  ndvi: ['NDVI', 'index.ndvi'],
  ndwi: ['NDWI', 'index.ndwi'],
  ndre: ['NDRE', 'index.ndre'],
  ndmi: ['NDMI', 'index.ndmi'],
  bsi:  ['BSI', 'index.bsi'],
  savi: ['SAVI', 'index.savi'],
  nbr:  ['NBR', 'index.nbr'],
}

// Descriptive 3-stop labels (low / mid / high) shown under the colorbar
const LABELS = {
  ndvi: ['legend.desert', 'legend.sparse', 'legend.dense'],
  ndwi: ['legend.dry', 'legend.normal', 'legend.water'],
  ndre: ['legend.stress', '', 'legend.healthy'],
  ndmi: ['legend.dry', 'legend.normal', 'legend.moist'],
  bsi:  ['legend.covered', '', 'legend.bare'],
  savi: ['legend.desert', 'legend.sparse', 'legend.dense'],
  nbr:  ['legend.degradation', '', 'legend.healthy'],
}

function ToolIcon({ type }) {
  if (type === 'line') {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M3 15 7.5 10.5l3 2L17 5" />
        <circle cx="3" cy="15" r="1.3" /><circle cx="7.5" cy="10.5" r="1.3" />
        <circle cx="10.5" cy="12.5" r="1.3" /><circle cx="17" cy="5" r="1.3" />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="m4 6 5-3 7 4-2 8-7 2-4-5 1-6Z" />
      <circle cx="4" cy="6" r="1.2" /><circle cx="9" cy="3" r="1.2" />
      <circle cx="16" cy="7" r="1.2" /><circle cx="14" cy="15" r="1.2" />
      <circle cx="7" cy="17" r="1.2" /><circle cx="3" cy="12" r="1.2" />
    </svg>
  )
}

export default function LayerPanel({
  layers, activeLayer, onSelect, opacity, onOpacityChange,
  drawMode, onToggleDraw, onClearZone, onFinishDraw, hasZone, drawPointCount = 0,
  lineDrawMode, onToggleLineDraw, onClearLine, onFinishLineDraw, hasLine, lineDrawPointCount = 0,
  lineDisabled = false,
  savedZones = [], activeZoneId = null, zoneDirty = false, zoneEditMode = false, zoneStorageError = null,
  savedZonesCloudMode = false,
  onSaveZone, onUpdateZone, onOpenZone, onRenameZone, onDeleteZone, onToggleZoneEdit,
  forecastMode = false,
  onClose,
}) {
  const { t } = useI18n()
  const indexIds = Object.keys(layers).length ? Object.keys(layers) : Object.keys(NAMES).filter((id) => id !== 'satellite')
  const ids = indexIds.filter((id) => id !== 'satellite')
  const active = layers[activeLayer]

  return (
    <aside className="panel panel-left" id="layer-panel" aria-label={t('layer.panelAria')}>
      <div className="panel-header panel-header-row">
        <div>
          <span className="panel-eyebrow">{forecastMode ? t('layer.experimental') : t('layer.mapData')}</span>
          <h2 className="panel-title">{t('layer.title')}</h2>
        </div>
        <button className="panel-close" type="button" onClick={onClose} aria-label={t('layer.hidePanel')}>×</button>
      </div>

      <section className="panel-section" aria-labelledby="layers-heading">
        <div className="section-heading" id="layers-heading">
          <span>{t('layer.mapLayers')}</span>
          <span className="section-count">{ids.length + 1}</span>
        </div>
        <button
          type="button"
          className={`layer-btn base-layer-btn ${activeLayer === 'satellite' ? 'active' : ''}`}
          onClick={() => onSelect('satellite')}
          disabled={forecastMode}
          aria-pressed={activeLayer === 'satellite'}
          title={forecastMode ? t('layer.forecastIndexOnly') : undefined}
        >
          <span className="layer-swatch" style={{ background: SWATCH.satellite }} />
          <span className="layer-info">
            <span className="layer-name">{t('layer.sentinel')}</span>
            <span className="layer-desc">{t('layer.naturalColors')}</span>
          </span>
          <span className="layer-badge" aria-hidden="true">{activeLayer === 'satellite' ? '✓' : ''}</span>
        </button>

        <div className="index-layer-grid">
        {ids.map((id) => {
          const [name, descKey] = NAMES[id] || [id.toUpperCase(), '']
          const desc = descKey ? t(descKey) : ''
          return (
            <button
              key={id}
              type="button"
              className={`layer-btn index-layer-btn ${id === activeLayer ? 'active' : ''}`}
              onClick={() => onSelect(id)}
              aria-pressed={id === activeLayer}
              title={`${name} — ${desc}`}
            >
              <span className="layer-swatch" style={{ background: SWATCH[id] }} />
              <span className="layer-info">
                <span className="layer-name">{name}</span>
                <span className="layer-desc">{desc}</span>
              </span>
              <span className="layer-badge" aria-hidden="true">{id === activeLayer ? '✓' : ''}</span>
            </button>
          )
        })}
        </div>
      </section>

      {forecastMode ? (
        <div className="forecast-mode-hint">
          <strong>{t('layer.linearPrototype')}</strong>
          <span>{t('layer.linearPrototypeHint')}</span>
        </div>
      ) : (
        <section className="panel-section" aria-labelledby="analysis-tools-heading">
          <div className="section-heading" id="analysis-tools-heading">{t('layer.analysisTools')}</div>
          <div className="analysis-tool-grid">
            <button
              type="button"
              className={`analysis-tool-button ${drawMode ? 'active' : ''}`}
              onClick={onToggleDraw}
              aria-pressed={drawMode}
            >
              <ToolIcon type="zone" />
              <span><strong>{t('layer.zone')}</strong><small>{drawMode ? t('common.cancel') : t('layer.zoneHint')}</small></span>
            </button>
            <button
              type="button"
              className={`analysis-tool-button ${lineDrawMode ? 'active' : ''}`}
              onClick={onToggleLineDraw}
              disabled={lineDisabled}
              aria-pressed={lineDrawMode}
              title={lineDisabled ? t('layer.selectIndexProfile') : undefined}
            >
              <ToolIcon type="line" />
              <span><strong>{t('layer.profile')}</strong><small>{lineDrawMode ? t('common.cancel') : t('layer.profileHint')}</small></span>
            </button>
          </div>

          {(drawMode || lineDrawMode) && (
            <div className="draw-workflow" role="status">
              <div className="draw-workflow-copy">
                <strong>{drawMode ? t('layer.buildBoundary') : t('layer.drawProfile')}</strong>
                <span>
                  {drawMode
                    ? t('layer.pointsZone', { count: drawPointCount })
                    : t('layer.pointsLine', { count: lineDrawPointCount })}
                </span>
              </div>
              <button
                type="button"
                className="zone-tool-btn zone-tool-finish"
                onClick={drawMode ? onFinishDraw : onFinishLineDraw}
                disabled={drawMode ? drawPointCount < 3 : lineDrawPointCount < 2}
              >
                {t('layer.finish')}
              </button>
            </div>
          )}

          {(hasZone || hasLine) && (
            <div className="clear-actions">
              {hasZone && <button type="button" onClick={onClearZone}>{t('layer.clearZone')}</button>}
              {hasLine && <button type="button" onClick={onClearLine}>{t('layer.clearLine')}</button>}
            </div>
          )}
        </section>
      )}

      {!forecastMode && (
        <SavedZones
          zones={savedZones}
          activeZoneId={activeZoneId}
          hasZone={hasZone}
          dirty={zoneDirty}
          editMode={zoneEditMode}
          storageError={zoneStorageError}
          cloudMode={savedZonesCloudMode}
          onSave={onSaveZone}
          onUpdate={onUpdateZone}
          onOpen={onOpenZone}
          onRename={onRenameZone}
          onDelete={onDeleteZone}
          onToggleEdit={onToggleZoneEdit}
        />
      )}

      <section className="panel-section display-section" aria-labelledby="display-heading">
        <div className="section-heading" id="display-heading">
          <span>{t('layer.display')}</span>
          <span className="opacity-val">{Math.round(opacity * 100)}%</span>
        </div>
        <label className="sr-only" htmlFor="layer-opacity">{t('layer.opacity')}</label>
        <div className="opacity-row">
          <input
            id="layer-opacity" type="range" className="slider" min="0" max="100"
            value={Math.round(opacity * 100)}
            aria-valuetext={`${Math.round(opacity * 100)}%`}
            onChange={(e) => onOpacityChange(Number(e.target.value) / 100)}
          />
        </div>

        {active && active.range && (
          <div className="colorbar-wrap">
            <div className="colorbar-title">{t('layer.legend', { name: (NAMES[activeLayer] || [activeLayer])[0] })}</div>
            <div className="colorbar" style={{ background: active.cssGradient }} />
            <div className="colorbar-labels">
              {(LABELS[activeLayer] || [active.range[0], 0, active.range[1]]).map((label, i) => (
                <span key={i}>{typeof label === 'string' && label ? t(label) : label}</span>
              ))}
            </div>
          </div>
        )}
      </section>
    </aside>
  )
}
