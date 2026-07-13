import { useState } from 'react'
import { fetchZoneReport } from '../api'
import { useI18n } from '../i18n.jsx'

const LULC_COLORS = {
  agriculture:       '#4ade80',
  bare_soil:         '#fbbf24',
  dense_vegetation:  '#16a34a',
  sparse_vegetation: '#86efac',
  urban:             '#94a3b8',
  water:             '#60a5fa',
}
const LULC_ORDER = ['agriculture', 'urban', 'dense_vegetation', 'sparse_vegetation', 'bare_soil', 'water']

const INDEX_LABELS = {
  ndvi: 'NDVI', ndre: 'NDRE', ndwi: 'NDWI', ndmi: 'NDMI', bsi: 'BSI',
}
const INDEX_ORDER = ['ndvi', 'ndre', 'ndwi', 'ndmi', 'bsi']

const LAYER_NAMES = { ndvi: 'NDVI', ndwi: 'NDWI', ndre: 'NDRE', ndmi: 'NDMI', bsi: 'BSI' }

const DARK   = [15, 23, 42]     // #0f172a
const ACCENT = [59, 130, 246]   // #3B82F6
const WHITE  = [255, 255, 255]
const GRAY   = [148, 163, 184]
const TEXT   = [30, 41, 59]

// jsPDF's built-in helvetica/times/courier only cover WinAnsi (Latin-1) — Cyrillic
// glyphs are silently dropped. We embed Verdana (regular + bold), which ships with
// full Cyrillic coverage, as a custom VFS font instead of relying on setLanguage()
// (that only sets PDF metadata, it has no effect on glyph rendering).
const FONT_NAME = 'Verdana'

function registerFont(doc, regularBase64, boldBase64) {
  doc.addFileToVFS('Verdana-Regular.ttf', regularBase64)
  doc.addFont('Verdana-Regular.ttf', FONT_NAME, 'normal')
  doc.addFileToVFS('Verdana-Bold.ttf', boldBase64)
  doc.addFont('Verdana-Bold.ttf', FONT_NAME, 'bold')
  doc.setFont(FONT_NAME, 'normal')
}

function hexToRgb(hex) {
  const v = hex.replace('#', '')
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)]
}

function sectionTitle(doc, title, pageW) {
  doc.setFillColor(...DARK)
  doc.rect(0, 0, pageW, 22, 'F')
  doc.setTextColor(...WHITE)
  doc.setFontSize(14)
  doc.setFont(FONT_NAME, 'bold')
  doc.text(title, 14, 14)
  doc.setFont(FONT_NAME, 'normal')
}

function addFooter(doc, pageLabel, footerText) {
  const w = doc.internal.pageSize.getWidth()
  const h = doc.internal.pageSize.getHeight()
  doc.setFont(FONT_NAME, 'normal')
  doc.setFontSize(8)
  doc.setTextColor(...GRAY)
  doc.text(footerText, 14, h - 8)
  doc.text(pageLabel, w - 14, h - 8, { align: 'right' })
}

// Splits Groq's "1. TITLE\n...body...\n\n2. TITLE\n...\n" output into {title, body} blocks.
// LLM output doesn't always follow the requested format exactly (markdown **bold**,
// "Раздел N." instead of bare "N.", mixed case) — tolerate those variants rather than
// silently falling back to one untitled blob.
function splitAnalysisSections(text, fallback) {
  if (!text) return [{ title: null, body: fallback }]
  const clean = text.replace(/\r\n/g, '\n')
  const re = /(?:^|\n)[ \t]*\*{0,2}[ \t]*(?:(?:Раздел|Section|Бөлім)[ \t]+)?(\d)\.[ \t]*([^\n*]+?)[ \t]*\*{0,2}[ \t]*\n/gi
  const matches = [...clean.matchAll(re)]
  if (matches.length === 0) return [{ title: null, body: sanitizeBody(clean) }]
  const sections = []
  for (let i = 0; i < matches.length; i++) {
    const start = matches[i].index + matches[i][0].length
    const end = i + 1 < matches.length ? matches[i + 1].index : clean.length
    sections.push({
      title: `${matches[i][1]}. ${matches[i][2].trim().toUpperCase()}`,
      body:  sanitizeBody(clean.slice(start, end)),
    })
  }
  return sections
}

function sanitizeBody(s) {
  return s
    .replace(/\*\*/g, '')
    .replace(/^[ \t]*[*-][ \t]+/gm, '• ')
    .trim()
}

function safeFilePart(value) {
  return (value || 'zone')
    .trim()
    .replace(/[<>:"/\\|?*\u0000-\u001F]/g, '_')
    .replace(/\s+/g, '_')
    .slice(0, 80) || 'zone'
}

function downloadBlob(content, type, filename) {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

function csvCell(value, protectText = false) {
  if (value == null) return ''
  let text = String(value)
  if (protectText && /^[=+\-@]/.test(text)) text = `'${text}`
  if (/[",\r\n]/.test(text)) text = `"${text.replace(/"/g, '""')}"`
  return text
}

function buildCsv({ zoneName, stats, period, timeSeries, unnamed }) {
  const header = [
    'zone_name', 'period_id', 'year', 'index', 'mean', 'min', 'max',
    'std', 'p10', 'p90', 'area_ha', 'pixel_count',
  ]
  const observations = timeSeries?.observations?.length
    ? timeSeries.observations
    : [{
        period_id: period,
        year: period?.match(/\d{4}/)?.[0] || '',
        area_ha: stats.area_ha,
        pixel_count: stats.pixel_count,
        indices: stats.indices,
      }]
  const rows = [header.join(',')]
  observations.forEach((observation) => {
    Object.entries(observation.indices || {}).forEach(([index, values]) => {
      rows.push([
        csvCell(zoneName || unnamed, true),
        csvCell(observation.period_id),
        csvCell(observation.year),
        csvCell(index),
        csvCell(values.mean), csvCell(values.min), csvCell(values.max),
        csvCell(values.std), csvCell(values.p10), csvCell(values.p90),
        csvCell(observation.area_ha), csvCell(observation.pixel_count),
      ].join(','))
    })
  })
  return `\uFEFF${rows.join('\r\n')}\r\n`
}

function buildPdf({ jsPDF, fonts, stats, activeLayer, period, mapImageBase64, groqAnalysis, aiModel, t, localeTag, formatNumber, periodLabel }) {
  const doc = new jsPDF({ unit: 'mm', format: 'a4' })
  registerFont(doc, fonts.VERDANA_BASE64, fonts.VERDANA_BOLD_BASE64)

  const pageW = doc.internal.pageSize.getWidth()
  const pageH = doc.internal.pageSize.getHeight()
  const dateStr = new Date().toLocaleDateString(localeTag, { year: 'numeric', month: 'long', day: 'numeric' })
  const fileDateStr = new Date().toISOString().slice(0, 10)

  // ── Page 1 — Cover ──────────────────────────────────────────
  doc.setFillColor(...DARK)
  doc.rect(0, 0, pageW, pageH, 'F')

  doc.setFont(FONT_NAME, 'bold')
  doc.setFontSize(30)
  doc.setTextColor(...WHITE)
  doc.text('GeoAI TKO', pageW / 2, 70, { align: 'center' })

  doc.setFont(FONT_NAME, 'normal')
  doc.setFontSize(11)
  doc.setTextColor(...GRAY)
  doc.text(t('report.platform'), pageW / 2, 80, { align: 'center' })

  doc.setFont(FONT_NAME, 'bold')
  doc.setFontSize(20)
  doc.setTextColor(...WHITE)
  doc.text(t('report.title'), pageW / 2, 120, { align: 'center' })

  doc.setFont(FONT_NAME, 'normal')
  doc.setFontSize(11)
  doc.setTextColor(...GRAY)
  const coverLines = [
    t('report.region'),
    `${t('report.date')}: ${dateStr}`,
    `${t('report.zoneArea')}: ${formatNumber(stats.area_ha ?? 0)} ${t('unit.ha')}`,
    `${t('report.dataSource')}: Sentinel-2 L2A, ${periodLabel(period)}`,
  ]
  coverLines.forEach((line, i) => doc.text(line, pageW / 2, 145 + i * 7, { align: 'center' }))

  doc.setDrawColor(...GRAY)
  doc.line(pageW / 2 - 40, pageH - 30, pageW / 2 + 40, pageH - 30)
  doc.setFontSize(9)
  doc.text('Powered by Sentinel-2 • GeoAI', pageW / 2, pageH - 22, { align: 'center' })

  // ── Page 2 — Map ────────────────────────────────────────────
  doc.addPage()
  sectionTitle(doc, t('report.mapTitle'), pageW)

  if (mapImageBase64) {
    const imgW = pageW - 28
    const imgH = imgW * 0.62
    try { doc.addImage(mapImageBase64, 'PNG', 14, 32, imgW, imgH) } catch { /* skip broken capture */ }
    let y = 32 + imgH + 12
    doc.setFontSize(10)
    doc.setTextColor(...TEXT)
    const activeLayerName = LAYER_NAMES[activeLayer] || (activeLayer === 'satellite' ? t('layer.satellite') : (activeLayer || 'NDVI').toUpperCase())
    doc.text(`${t('report.activeLayer')}: ${activeLayerName}`, 14, y)
    y += 6
    doc.setTextColor(...GRAY)
    doc.text(`CRS: EPSG:32641 • ${t('report.resolution')}`, 14, y)
    y += 6
    doc.text(`${t('report.source')}: Sentinel-2 L2A, ${periodLabel(period)}`, 14, y)
  } else {
    doc.setFontSize(10)
    doc.setTextColor(...GRAY)
    doc.text(t('report.mapUnavailable'), 14, 40)
  }

  // ── Page 3 — Indices + LULC ─────────────────────────────────
  doc.addPage()
  sectionTitle(doc, t('report.indicesTitle'), pageW)
  let y = 34
  INDEX_ORDER.filter((k) => stats.indices?.[k]).forEach((key) => {
    const s = stats.indices[key]
    const code = INDEX_LABELS[key]
    const label = t(`index.${key}`)

    doc.setFontSize(11)
    doc.setTextColor(...TEXT)
    doc.text(`${label} (${code})`, 14, y)
    doc.setTextColor(...ACCENT)
    doc.setFont(FONT_NAME, 'bold')
    doc.text(s.mean.toFixed(3), pageW - 14, y, { align: 'right' })
    doc.setFont(FONT_NAME, 'normal')

    y += 4
    const barX = 14, barW = pageW - 28, barH = 3
    doc.setFillColor(226, 232, 240)
    doc.roundedRect(barX, y, barW, barH, 1, 1, 'F')
    const pct = Math.max(0, Math.min(1, (s.mean + 1) / 2))
    doc.setFillColor(...ACCENT)
    doc.roundedRect(barX, y, Math.max(2, barW * pct), barH, 1, 1, 'F')

    y += 7
    doc.setFontSize(8.5)
    doc.setTextColor(...GRAY)
    doc.text(`${t('zone.min')}: ${s.min.toFixed(2)}   ${t('zone.average')}: ${s.mean.toFixed(3)}   ${t('zone.max')}: ${s.max.toFixed(2)}`, 14, y)
    y += 10
  })

  y += 2
  doc.setDrawColor(226, 232, 240)
  doc.line(14, y, pageW - 14, y)
  y += 10

  doc.setFont(FONT_NAME, 'bold')
  doc.setFontSize(13)
  doc.setTextColor(...DARK)
  doc.text(t('zone.landClass').toLocaleUpperCase(localeTag), 14, y)
  doc.setFont(FONT_NAME, 'normal')
  y += 8

  const barX = 14, barW = pageW - 28, barH = 6
  let xCursor = barX
  LULC_ORDER.filter((k) => stats.lulc?.[k]).forEach((key) => {
    const pct = (stats.lulc[key].percent || 0) / 100
    const segW = barW * pct
    doc.setFillColor(...hexToRgb(LULC_COLORS[key]))
    doc.rect(xCursor, y, segW, barH, 'F')
    xCursor += segW
  })
  y += barH + 10

  doc.setFontSize(10)
  LULC_ORDER.filter((k) => stats.lulc?.[k] && stats.lulc[k].pixels > 0).forEach((key) => {
    const v = stats.lulc[key]
    doc.setFillColor(...hexToRgb(LULC_COLORS[key]))
    doc.circle(16, y - 1.3, 1.6, 'F')
    doc.setTextColor(...TEXT)
    doc.text(t(`lulc.${key}`), 22, y)
    doc.text(`${formatNumber(v.area_ha)} ${t('unit.ha')}`, pageW - 50, y, { align: 'right' })
    doc.text(`${v.percent}%`, pageW - 14, y, { align: 'right' })
    y += 7
  })

  // ── Page 4+ — AI analysis ───────────────────────────────────
  doc.addPage()
  sectionTitle(doc, t('report.analysisTitle'), pageW)
  doc.setFontSize(9)
  doc.setTextColor(...GRAY)
  doc.text(`${t('report.generatedBy')} (${aiModel || t('report.localAnalysis')})`, 14, 30)

  let yy = 40
  const maxWidth = pageW - 28
  splitAnalysisSections(groqAnalysis, t('error.analysisUnavailable')).forEach(({ title, body }) => {
    if (title) {
      if (yy > pageH - 35) { doc.addPage(); yy = 20 }
      doc.setFont(FONT_NAME, 'bold')
      doc.setFontSize(11.5)
      doc.setTextColor(...DARK)
      doc.text(title, 14, yy)
      yy += 5
      doc.setDrawColor(...ACCENT)
      doc.setLineWidth(0.6)
      doc.line(14, yy, 14 + doc.getTextWidth(title), yy)
      yy += 6
      doc.setFont(FONT_NAME, 'normal')
    }
    doc.setFontSize(10)
    doc.setTextColor(...TEXT)
    const paragraphs = body.split(/\n+/).map((p) => p.trim()).filter(Boolean)
    paragraphs.forEach((para) => {
      doc.splitTextToSize(para, maxWidth).forEach((line) => {
        if (yy > pageH - 20) { doc.addPage(); yy = 20 }
        doc.text(line, 14, yy)
        yy += 5.2
      })
      yy += 2
    })
    yy += 4
  })

  doc.setFont(FONT_NAME, 'normal')
  doc.setFontSize(8)
  doc.setTextColor(...GRAY)
  doc.text(`GeoAI TKO • ${dateStr} • ${t('report.autoGenerated')}`, pageW / 2, pageH - 14, { align: 'center' })

  const total = doc.internal.getNumberOfPages()
  for (let p = 2; p <= total; p++) {
    doc.setPage(p)
    addFooter(doc, t('report.page', { page: p }), t('report.footer'))
  }

  doc.save(`GeoAI_TKO_Report_${fileDateStr}.pdf`)
}

export default function ZoneReport({ geometry, stats, activeLayer, period, zoneName, timeSeries }) {
  const { locale, localeTag, t, formatNumber, periodLabel } = useI18n()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  if (!stats) return null

  async function handlePdfDownload() {
    setLoading(true)
    setError(null)
    try {
      const [pdfModule, canvasModule, fonts] = await Promise.all([
        import('jspdf'),
        import('html2canvas'),
        import('../assets/fonts/verdanaBase64.js'),
      ])
      const jsPDF = pdfModule.default
      const html2canvas = canvasModule.default
      const mapEl = document.getElementById('map')
      let mapImageBase64 = null
      if (mapEl) {
        const canvas = await html2canvas(mapEl, {
          useCORS: true, allowTaint: false, willReadFrequently: true, logging: false, scale: 2,
        })
        mapImageBase64 = canvas.toDataURL('image/png')
      }

      const { groq_analysis, model } = await fetchZoneReport({
        geometry, zoneStats: stats, activeLayer, period, locale,
      })

      buildPdf({
        jsPDF, fonts, stats, activeLayer, period, mapImageBase64,
        groqAnalysis: groq_analysis, aiModel: model, t, localeTag, formatNumber, periodLabel,
      })
    } catch (e) {
      setError(e.message || t('report.errorPdf'))
    } finally {
      setLoading(false)
    }
  }

  function handleCsvDownload() {
    setError(null)
    try {
      const csv = buildCsv({ zoneName, stats, period, timeSeries, unnamed: t('common.unnamed') })
      downloadBlob(csv, 'text/csv;charset=utf-8', `${safeFilePart(zoneName)}_analysis.csv`)
    } catch (e) {
      setError(e.message || t('report.errorCsv'))
    }
  }

  function handleGeoJsonDownload() {
    setError(null)
    try {
      const feature = {
        type: 'Feature',
        properties: {
          name: zoneName || t('common.unnamed'),
          exported_at: new Date().toISOString(),
          period,
          active_layer: activeLayer,
          statistics: stats,
          time_series: timeSeries?.observations || [],
        },
        geometry,
      }
      downloadBlob(
        JSON.stringify(feature, null, 2),
        'application/geo+json;charset=utf-8',
        `${safeFilePart(zoneName)}_analysis.geojson`,
      )
    } catch (e) {
      setError(e.message || t('report.errorGeojson'))
    }
  }

  return (
    <div className="zone-report">
      <div className="zone-block-title">{t('report.export')}</div>
      <div className="zone-export-grid">
        <button className="zone-tool-btn zone-report-btn" onClick={handlePdfDownload} disabled={loading}>
          {loading ? t('report.preparing') : 'PDF'}
        </button>
        <button className="zone-tool-btn zone-export-btn" onClick={handleCsvDownload}>CSV</button>
        <button className="zone-tool-btn zone-export-btn" onClick={handleGeoJsonDownload}>GeoJSON</button>
      </div>
      {error && <div className="zone-error" style={{ marginTop: 8 }}>{error}</div>}
    </div>
  )
}
