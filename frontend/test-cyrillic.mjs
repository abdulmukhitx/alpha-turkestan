import { jsPDF } from 'jspdf'
import { VERDANA_BASE64, VERDANA_BOLD_BASE64 } from './src/assets/fonts/verdanaBase64.js'
import { writeFileSync } from 'fs'

const doc = new jsPDF({ unit: 'mm', format: 'a4' })
doc.addFileToVFS('Verdana-Regular.ttf', VERDANA_BASE64)
doc.addFont('Verdana-Regular.ttf', 'Verdana', 'normal')
doc.addFileToVFS('Verdana-Bold.ttf', VERDANA_BOLD_BASE64)
doc.addFont('Verdana-Bold.ttf', 'Verdana', 'bold')

doc.setFont('Verdana', 'bold')
doc.setFontSize(16)
doc.text('1. ОБЩАЯ ХАРАКТЕРИСТИКА ЗОНЫ', 14, 20)

doc.setFont('Verdana', 'normal')
doc.setFontSize(11)
doc.text('Туркестанская область, Казахстан — мониторинг земель Sentinel-2.', 14, 30)
doc.text('Площадь зоны: 11 569,92 га. Среднее NDVI: 0.087, NDWI: -0.186.', 14, 37)

const out = doc.output('arraybuffer')
writeFileSync('test-cyrillic.pdf', Buffer.from(out))
console.log('wrote', out.byteLength, 'bytes')
