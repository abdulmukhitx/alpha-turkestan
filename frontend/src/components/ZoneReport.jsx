import { useState } from 'react'
import jsPDF from 'jspdf'
import html2canvas from 'html2canvas'
import { fetchZoneReport } from '../api'
import { VERDANA_BASE64, VERDANA_BOLD_BASE64 } from '../assets/fonts/verdanaBase64.js'

const LULC_LABELS = {
  agriculture:       'Сельхоз угодья',
  bare_soil:         'Голая почва',
  dense_vegetation:  'Густая растительность',
  sparse_vegetation: 'Разреженная растительность',
  urban:             'Застройка',
  water:             'Водные объекты',
}
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
  ndvi: ['NDVI', 'Растительность'],
  ndre: ['NDRE', 'Стресс растений'],
  ndwi: ['NDWI', 'Водные ресурсы'],
  ndmi: ['NDMI', 'Влажность почвы'],
  bsi:  ['BSI',  'Голая почва'],
}
const INDEX_ORDER = ['ndvi', 'ndre', 'ndwi', 'ndmi', 'bsi']

const LAYER_NAMES = { ndvi: 'NDVI', ndwi: 'NDWI', ndre: 'NDRE', ndmi: 'NDMI', bsi: 'BSI', satellite: 'Спутниковый снимок' }

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

function registerFont(doc) {
  doc.addFileToVFS('Verdana-Regular.ttf', VERDANA_BASE64)
  doc.addFont('Verdana-Regular.ttf', FONT_NAME, 'normal')
  doc.addFileToVFS('Verdana-Bold.ttf', VERDANA_BOLD_BASE64)
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

function addFooter(doc, pageLabel) {
  const w = doc.internal.pageSize.getWidth()
  const h = doc.internal.pageSize.getHeight()
  doc.setFont(FONT_NAME, 'normal')
  doc.setFontSize(8)
  doc.setTextColor(...GRAY)
  doc.text('GeoAI TKO | Мониторинг земель Туркестанской области', 14, h - 8)
  doc.text(pageLabel, w - 14, h - 8, { align: 'right' })
}

// Splits Groq's "1. TITLE\n...body...\n\n2. TITLE\n...\n" output into {title, body} blocks.
// LLM output doesn't always follow the requested format exactly (markdown **bold**,
// "Раздел N." instead of bare "N.", mixed case) — tolerate those variants rather than
// silently falling back to one untitled blob.
function splitAnalysisSections(text) {
  if (!text) return [{ title: null, body: 'Анализ недоступен.' }]
  const clean = text.replace(/\r\n/g, '\n')
  const re = /(?:^|\n)[ \t]*\*{0,2}[ \t]*(?:Раздел[ \t]+)?(\d)\.[ \t]*([^\n*]+?)[ \t]*\*{0,2}[ \t]*\n/gi
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

function buildPdf({ stats, activeLayer, mapImageBase64, groqAnalysis }) {
  const doc = new jsPDF({ unit: 'mm', format: 'a4' })
  registerFont(doc)

  const pageW = doc.internal.pageSize.getWidth()
  const pageH = doc.internal.pageSize.getHeight()
  const dateStr = new Date().toLocaleDateString('ru-RU', { year: 'numeric', month: 'long', day: 'numeric' })
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
  doc.text('Платформа мониторинга земель', pageW / 2, 80, { align: 'center' })

  doc.setFont(FONT_NAME, 'bold')
  doc.setFontSize(20)
  doc.setTextColor(...WHITE)
  doc.text('ОТЧЁТ ПО ЗОНАЛЬНОМУ АНАЛИЗУ', pageW / 2, 120, { align: 'center' })

  doc.setFont(FONT_NAME, 'normal')
  doc.setFontSize(11)
  doc.setTextColor(...GRAY)
  const coverLines = [
    'Туркестанская область, Казахстан',
    `Дата: ${dateStr}`,
    `Площадь зоны: ${(stats.area_ha ?? 0).toLocaleString('ru-RU')} га`,
    'Источник данных: Sentinel-2 L2A, 2023',
  ]
  coverLines.forEach((line, i) => doc.text(line, pageW / 2, 145 + i * 7, { align: 'center' }))

  doc.setDrawColor(...GRAY)
  doc.line(pageW / 2 - 40, pageH - 30, pageW / 2 + 40, pageH - 30)
  doc.setFontSize(9)
  doc.text('Powered by Sentinel-2 • Groq AI', pageW / 2, pageH - 22, { align: 'center' })

  // ── Page 2 — Map ────────────────────────────────────────────
  doc.addPage()
  sectionTitle(doc, 'КАРТА ЗОНЫ АНАЛИЗА', pageW)

  if (mapImageBase64) {
    const imgW = pageW - 28
    const imgH = imgW * 0.62
    try { doc.addImage(mapImageBase64, 'PNG', 14, 32, imgW, imgH) } catch { /* skip broken capture */ }
    let y = 32 + imgH + 12
    doc.setFontSize(10)
    doc.setTextColor(...TEXT)
    doc.text(`Активный слой: ${LAYER_NAMES[activeLayer] || (activeLayer || 'NDVI').toUpperCase()}`, 14, y)
    y += 6
    doc.setTextColor(...GRAY)
    doc.text('CRS: EPSG:32641 • Разрешение: 10м/пикс', 14, y)
    y += 6
    doc.text('Источник: Sentinel-2 L2A, лето 2023', 14, y)
  } else {
    doc.setFontSize(10)
    doc.setTextColor(...GRAY)
    doc.text('Скриншот карты недоступен.', 14, 40)
  }

  // ── Page 3 — Indices + LULC ─────────────────────────────────
  doc.addPage()
  sectionTitle(doc, 'СПЕКТРАЛЬНЫЕ ИНДЕКСЫ', pageW)
  let y = 34
  INDEX_ORDER.filter((k) => stats.indices?.[k]).forEach((key) => {
    const s = stats.indices[key]
    const [code, label] = INDEX_LABELS[key]

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
    doc.text(`Мин: ${s.min.toFixed(2)}   Среднее: ${s.mean.toFixed(3)}   Макс: ${s.max.toFixed(2)}`, 14, y)
    y += 10
  })

  y += 2
  doc.setDrawColor(226, 232, 240)
  doc.line(14, y, pageW - 14, y)
  y += 10

  doc.setFont(FONT_NAME, 'bold')
  doc.setFontSize(13)
  doc.setTextColor(...DARK)
  doc.text('КЛАССИФИКАЦИЯ ЗЕМЕЛЬ', 14, y)
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
    doc.text(LULC_LABELS[key] || key, 22, y)
    doc.text(`${v.area_ha.toLocaleString('ru-RU')} га`, pageW - 50, y, { align: 'right' })
    doc.text(`${v.percent}%`, pageW - 14, y, { align: 'right' })
    y += 7
  })

  // ── Page 4+ — AI analysis ───────────────────────────────────
  doc.addPage()
  sectionTitle(doc, 'АНАЛИТИЧЕСКОЕ ЗАКЛЮЧЕНИЕ', pageW)
  doc.setFontSize(9)
  doc.setTextColor(...GRAY)
  doc.text('Сформировано Groq AI (llama3-8b-8192)', 14, 30)

  let yy = 40
  const maxWidth = pageW - 28
  splitAnalysisSections(groqAnalysis).forEach(({ title, body }) => {
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
  doc.text(`GeoAI TKO • ${dateStr} • Автоматически сгенерировано на основе Sentinel-2`, pageW / 2, pageH - 14, { align: 'center' })

  const total = doc.internal.getNumberOfPages()
  for (let p = 2; p <= total; p++) {
    doc.setPage(p)
    addFooter(doc, `Стр. ${p}`)
  }

  doc.save(`GeoAI_TKO_Report_${fileDateStr}.pdf`)
}

export default function ZoneReport({ geometry, stats, activeLayer }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  if (!stats) return null

  async function handleDownload() {
    setLoading(true)
    setError(null)
    try {
      const mapEl = document.getElementById('map')
      let mapImageBase64 = null
      if (mapEl) {
        const canvas = await html2canvas(mapEl, {
          useCORS: true, allowTaint: false, willReadFrequently: true, logging: false, scale: 2,
        })
        mapImageBase64 = canvas.toDataURL('image/png')
      }

      const { groq_analysis } = await fetchZoneReport({
        geometry, zoneStats: stats, activeLayer, mapImageBase64,
      })

      buildPdf({ stats, activeLayer, mapImageBase64, groqAnalysis: groq_analysis })
    } catch (e) {
      setError(e.message || 'Не удалось сформировать отчёт')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="zone-report">
      <button className="zone-tool-btn zone-report-btn" onClick={handleDownload} disabled={loading}>
        {loading ? 'Генерируем отчёт...' : '📄 Скачать отчёт'}
      </button>
      {error && <div className="zone-error" style={{ marginTop: 8 }}>{error}</div>}
    </div>
  )
}
