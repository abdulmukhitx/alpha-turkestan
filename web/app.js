/* ══════════════════════════════════════════════════════════════
   GeoAI-TKO · app.js
   Архитектура: FastAPI бэкенд → imageOverlay → Leaflet
   Масштабируется до: XYZ тайлов, TiTiler, real-time Claude API
   ══════════════════════════════════════════════════════════════ */

'use strict';

// ── CONFIG ──────────────────────────────────────────────────────
const CONFIG = {
  // Бэкенд URL — поменяй если FastAPI на другом порту
  API_BASE: 'http://localhost:8000',

  // Bounds Туркестанской области [SW, NE] в WGS84
  TKO_BOUNDS: [[40.8, 67.5], [44.0, 71.5]],
  TKO_CENTER: [42.3, 69.5],
  TKO_ZOOM:   8,

  // Слои: id → конфигурация
  LAYERS: {
    ndvi: {
      label:    'NDVI · 2023',
      endpoint: '/rasters/ndvi',       // FastAPI endpoint
      colorbar: 'linear-gradient(90deg, #d73027, #f9f097, #1a9850)',
      min: -0.2, max: 0.8,
      description: 'Индекс растительности',
    },
    ndwi: {
      label:    'NDWI · 2023',
      endpoint: '/rasters/ndwi',
      colorbar: 'linear-gradient(90deg, #f7fbff, #2171b5)',
      min: -0.5, max: 0.6,
      description: 'Водный индекс',
    },
    // Добавляй новые слои сюда — всё остальное подхватится автоматически
    lst: {
      label:    'LST · 2023',
      endpoint: '/rasters/lst',
      colorbar: 'linear-gradient(90deg, #ffffcc, #fd8d3c, #bd0026)',
      min: 15, max: 55,
      description: 'Температура поверхности',
    },
    change: {
      label:    'Изменения 2017–2023',
      endpoint: '/rasters/change',
      colorbar: 'linear-gradient(90deg, #440154, #21908d, #fde725)',
      min: -1, max: 1,
      description: 'Детекция изменений',
    },
  },

  // Basemap тайлы
  BASEMAPS: {
    dark: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    sat:  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  },

  // Claude API (если используешь напрямую с фронтенда — для прода лучше проксировать через FastAPI)
  // Для MVP — анализ идёт через /api/analyze на FastAPI, который вызывает Claude
  ANALYZE_ENDPOINT: '/api/analyze',
};

// ── STATE ────────────────────────────────────────────────────────
const state = {
  activeLayer:  'ndvi',
  activeYear:   2023,
  layerOpacity: 0.85,
  activeBasemap:'dark',
  selectedPoint: null,
  overlays:     {},     // layerId → leaflet ImageOverlay
  marker:       null,
  isLoading:    false,
};

// ── ГЛОБАЛЬНАЯ ССЫЛКА НА КАРТУ (для кнопок в HTML onclick) ──────
let mapInstance = null;
let basemapLayer = null;
let boundaryLayer = null;

// ════════════════════════════════════════════════════════════════
//   BOOT SEQUENCE
// ════════════════════════════════════════════════════════════════
const BOOT_STEPS = [
  [100, 'Подключение к серверу...'],
  [500, 'Загрузка геоданных...'],
  [900, 'Инициализация карты...'],
  [1300, 'Подключение AI модуля...'],
  [1700, 'Система готова'],
];

async function boot() {
  const fill   = document.getElementById('boot-fill');
  const status = document.getElementById('boot-status');

  for (const [ms, msg] of BOOT_STEPS) {
    await delay(ms);
    const pct = Math.round((ms / 1700) * 100);
    fill.style.width   = pct + '%';
    status.textContent = msg;
  }

  await delay(200);
  document.getElementById('boot-screen').classList.add('fade-out');
  document.getElementById('app').style.display = 'block';

  await delay(600);
  document.getElementById('boot-screen').style.display = 'none';

  initMap();
  fetchHealth();
}

// ════════════════════════════════════════════════════════════════
//   MAP INIT
// ════════════════════════════════════════════════════════════════
function initMap() {
  mapInstance = L.map('map', {
    center:          CONFIG.TKO_CENTER,
    zoom:            CONFIG.TKO_ZOOM,
    zoomControl:     false,
    attributionControl: false,
    preferCanvas:    true,
  });

  // Подложка
  basemapLayer = L.tileLayer(CONFIG.BASEMAPS.dark, {
    maxZoom: 18, subdomains: 'abcd',
  }).addTo(mapInstance);

  // Граница области
  loadBoundary();

  // Начальный слой
  loadLayer(state.activeLayer);

  // События карты
  mapInstance.on('click',     onMapClick);
  mapInstance.on('mousemove', onMapMouseMove);
  mapInstance.on('zoomend',   onZoomChange);

  updateColorbar(state.activeLayer);
}

// ── Граница ТКО ─────────────────────────────────────────────────
async function loadBoundary() {
  try {
    const res  = await fetch(CONFIG.API_BASE + '/data/raw/turkestan_boundary.geojson');
    const data = await res.json();
    boundaryLayer = L.geoJSON(data, {
      style: {
        color:     '#00FF87',
        weight:    1.5,
        opacity:   0.5,
        fill:      false,
        dashArray: '6 4',
      },
    }).addTo(mapInstance);
  } catch (e) {
    // Если файл не нашёлся — рисуем bbox
    console.warn('Boundary GeoJSON не найден, используем bbox');
    L.rectangle(CONFIG.TKO_BOUNDS, {
      color: '#00FF87', weight: 1.5, fill: false,
      opacity: 0.4, dashArray: '6 4',
    }).addTo(mapInstance);
  }
}

// ── Загрузка растрового слоя ─────────────────────────────────────
async function loadLayer(layerId) {
  const cfg = CONFIG.LAYERS[layerId];
  if (!cfg) return;

  // Убрать предыдущий overlay того же слоя (если есть)
  if (state.overlays[layerId]) {
    mapInstance.removeLayer(state.overlays[layerId]);
    delete state.overlays[layerId];
  }

  try {
    // FastAPI отдаёт PNG напрямую по endpoint
    // URL формируем с параметром year для будущего переключения
    const url = `${CONFIG.API_BASE}${cfg.endpoint}?year=${state.activeYear}&format=png`;

    const overlay = L.imageOverlay(url, CONFIG.TKO_BOUNDS, {
      opacity:     state.layerOpacity,
      interactive: false,
      // className помогает стилизовать через CSS если надо
      className:   `layer-overlay layer-${layerId}`,
    });

    overlay.addTo(mapInstance);
    state.overlays[layerId] = overlay;

    // Обновить badge с годом
    const badge = document.getElementById(`badge-${layerId}`);
    if (badge) badge.textContent = state.activeYear;

  } catch (e) {
    console.error(`Ошибка загрузки слоя ${layerId}:`, e);
    showLayerError(layerId);
  }
}

// ── Показать только активный слой ────────────────────────────────
function showOnlyLayer(layerId) {
  Object.entries(state.overlays).forEach(([id, overlay]) => {
    if (id === layerId) {
      overlay.setOpacity(state.layerOpacity);
    } else {
      overlay.setOpacity(0);
    }
  });
}

// ════════════════════════════════════════════════════════════════
//   UI EVENTS
// ════════════════════════════════════════════════════════════════

// Переключить слой
function switchLayer(layerId, btn) {
  if (state.isLoading) return;

  state.activeLayer = layerId;

  // Обновить кнопки
  document.querySelectorAll('.layer-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');

  // Загрузить слой если ещё нет
  if (!state.overlays[layerId]) {
    loadLayer(layerId);
  }
  showOnlyLayer(layerId);
  updateColorbar(layerId);
}

// Прозрачность слоя
function setLayerOpacity(val) {
  state.layerOpacity = val / 100;
  document.getElementById('opacity-label').textContent = val + '%';
  const overlay = state.overlays[state.activeLayer];
  if (overlay) overlay.setOpacity(state.layerOpacity);
}

// Выбор года
function selectYear(year, btn) {
  state.activeYear = year;
  document.querySelectorAll('.year-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');

  // Перезагрузить все уже загруженные слои с новым годом
  const loadedIds = Object.keys(state.overlays);
  loadedIds.forEach(id => {
    mapInstance.removeLayer(state.overlays[id]);
    delete state.overlays[id];
  });
  loadLayer(state.activeLayer);
}

// Переключить подложку
function toggleBasemap() {
  const next = state.activeBasemap === 'dark' ? 'sat' : 'dark';
  state.activeBasemap = next;
  basemapLayer.setUrl(CONFIG.BASEMAPS[next]);
}

// Fitbounds
function fitTKO() {
  mapInstance.fitBounds(CONFIG.TKO_BOUNDS, { padding: [20, 20] });
}

// Refresh
function refreshData() {
  const loadedIds = Object.keys(state.overlays);
  loadedIds.forEach(id => {
    mapInstance.removeLayer(state.overlays[id]);
    delete state.overlays[id];
  });
  loadLayer(state.activeLayer);
}

// ── Colorbar ─────────────────────────────────────────────────────
function updateColorbar(layerId) {
  const cfg = CONFIG.LAYERS[layerId];
  if (!cfg) return;
  document.getElementById('colorbar-gradient').style.background = cfg.colorbar;
  document.getElementById('colorbar-title').textContent = cfg.label;
  document.getElementById('cb-min').textContent = cfg.min;
  document.getElementById('cb-max').textContent = cfg.max;
}

// ════════════════════════════════════════════════════════════════
//   MAP INTERACTIONS
// ════════════════════════════════════════════════════════════════

function onMapClick(e) {
  const { lat, lng } = e.latlng;

  // Проверить что точка внутри ТКО
  if (!isInsideTKO(lat, lng)) {
    showOutsideMessage();
    return;
  }

  state.selectedPoint = { lat, lng };

  // Маркер
  placeMarker(lat, lng);

  // Обновить координаты в topbar
  updateCoordDisplay(lat, lng);

  // Показать панель анализа
  showAnalysisPanel(lat, lng);
}

function onMapMouseMove(e) {
  const { lat, lng } = e.latlng;
  document.getElementById('lat-val').textContent = lat.toFixed(4) + '°N';
  document.getElementById('lon-val').textContent = lng.toFixed(4) + '°E';
  document.getElementById('map-pixel-info').textContent =
    `${lat.toFixed(5)}°N · ${lng.toFixed(5)}°E`;
}

function onZoomChange() {
  document.getElementById('map-zoom-label').textContent =
    `Zoom: ${mapInstance.getZoom()}`;
}

function isInsideTKO(lat, lng) {
  const [[s, w], [n, e]] = CONFIG.TKO_BOUNDS;
  return lat >= s && lat <= n && lng >= w && lng <= e;
}

function placeMarker(lat, lng) {
  if (state.marker) mapInstance.removeLayer(state.marker);

  // Пульсирующий div-маркер
  const icon = L.divIcon({
    className: '',
    html: `<div class="pulse-marker"></div>`,
    iconSize:   [14, 14],
    iconAnchor: [7, 7],
  });

  state.marker = L.marker([lat, lng], { icon, zIndexOffset: 1000 })
    .addTo(mapInstance);
}

function updateCoordDisplay(lat, lng) {
  document.getElementById('lat-val').textContent = lat.toFixed(4) + '°N';
  document.getElementById('lon-val').textContent = lng.toFixed(4) + '°E';
}

function showOutsideMessage() {
  // Простой всплывающий тост
  const toast = document.createElement('div');
  toast.style.cssText = `
    position:fixed; bottom:40px; left:50%; transform:translateX(-50%);
    background:rgba(13,19,32,0.95); border:1px solid rgba(255,183,3,0.4);
    color:#FFB703; font-family:'JetBrains Mono',monospace; font-size:11px;
    padding:8px 16px; border-radius:8px; z-index:9000;
    animation: fadeIn 0.2s ease;
  `;
  toast.textContent = 'Точка за пределами Туркестанской области';
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2500);
}

// ════════════════════════════════════════════════════════════════
//   AI ANALYSIS PANEL
// ════════════════════════════════════════════════════════════════

async function showAnalysisPanel(lat, lng) {
  // Скрыть приглашение, показать результат
  document.getElementById('click-prompt').style.display   = 'none';
  document.getElementById('analysis-result').style.display = 'block';

  // Координаты
  document.getElementById('result-coords').textContent =
    `${lat.toFixed(4)}°N · ${lng.toFixed(4)}°E`;

  // Сбросить предыдущие значения
  setMetricValue('m-ndvi',  '…');
  setMetricValue('m-ndwi',  '…');
  setMetricValue('m-class', '…');
  setMetricValue('m-trend', '…');
  document.getElementById('bar-ndvi').style.width = '0%';
  document.getElementById('bar-ndwi').style.width = '0%';
  showAILoading();
  document.getElementById('recommendations').style.display = 'none';

  try {
    // 1. Запрос значений пикселя с бэкенда
    const pixelData = await fetchPixelValues(lat, lng);

    // 2. Обновить метрики
    updateMetrics(pixelData);

    // 3. Нарисовать mini-chart
    drawTrendChart(pixelData.trend || []);

    // 4. AI интерпретация
    const aiText = await fetchAIAnalysis(lat, lng, pixelData);
    showAIText(aiText);

    // 5. Рекомендации (если есть)
    if (pixelData.recommendations && pixelData.recommendations.length > 0) {
      showRecommendations(pixelData.recommendations);
    }

  } catch (err) {
    console.error('Analysis error:', err);
    showAIText(
      'Не удалось получить данные для этой точки. ' +
      'Проверьте соединение с сервером или выберите другую точку.'
    );
    // Показываем демо-данные если API недоступен
    updateMetrics(getDemoPixelData(lat, lng));
    drawTrendChart(getDemoTrend());
  }
}

// ── Запрос значений пикселя с FastAPI ───────────────────────────
async function fetchPixelValues(lat, lng) {
  const res = await fetch(
    `${CONFIG.API_BASE}/api/pixel?lat=${lat}&lon=${lng}&year=${state.activeYear}`
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

// ── Запрос AI анализа с FastAPI (FastAPI → Claude API) ───────────
async function fetchAIAnalysis(lat, lng, pixelData) {
  const res = await fetch(`${CONFIG.API_BASE}${CONFIG.ANALYZE_ENDPOINT}`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      lat, lon: lng,
      year: state.activeYear,
      layer: state.activeLayer,
      ndvi: pixelData.ndvi,
      ndwi: pixelData.ndwi,
      land_class: pixelData.land_class,
    }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return data.analysis || data.text || 'Анализ недоступен';
}

// ── Обновить метрики в UI ────────────────────────────────────────
function updateMetrics(data) {
  const ndvi = data.ndvi ?? null;
  const ndwi = data.ndwi ?? null;

  if (ndvi !== null) {
    setMetricValue('m-ndvi', ndvi.toFixed(2));
    const pct = Math.max(0, Math.min(100, ((ndvi + 0.2) / 1.0) * 100));
    animateBar('bar-ndvi', pct);
  }

  if (ndwi !== null) {
    setMetricValue('m-ndwi', ndwi.toFixed(2));
    const pct = Math.max(0, Math.min(100, ((ndwi + 0.5) / 1.1) * 100));
    animateBar('bar-ndwi', pct);
  }

  if (data.land_class) setMetricValue('m-class', data.land_class);
  if (data.trend_label) setMetricValue('m-trend', data.trend_label);
}

function setMetricValue(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function animateBar(id, pct) {
  const el = document.getElementById(id);
  if (el) {
    requestAnimationFrame(() => { el.style.width = pct + '%'; });
  }
}

// ── AI loading/text ──────────────────────────────────────────────
function showAILoading() {
  const el = document.getElementById('ai-text');
  el.innerHTML = '<span class="ai-loading"><span class="dot-1">.</span><span class="dot-2">.</span><span class="dot-3">.</span></span>';
}

function showAIText(text) {
  const el = document.getElementById('ai-text');
  el.innerHTML = '';
  typewriterEffect(el, text, 18);
}

// Эффект печатающейся машинки для AI текста
function typewriterEffect(el, text, speed = 20) {
  let i = 0;
  let accumulated = '';
  const interval = setInterval(() => {
    if (i < text.length) {
      accumulated += text[i++];
      el.textContent = accumulated;
    } else {
      clearInterval(interval);
    }
  }, speed);
}

// ── Рекомендации ────────────────────────────────────────────────
function showRecommendations(recs) {
  const wrap = document.getElementById('recommendations');
  const list = document.getElementById('rec-list');
  list.innerHTML = '';
  recs.forEach(r => {
    const div = document.createElement('div');
    div.className = 'rec-item';
    div.textContent = r;
    list.appendChild(div);
  });
  wrap.style.display = 'block';
}

// ════════════════════════════════════════════════════════════════
//   MINI TREND CHART (Canvas)
// ════════════════════════════════════════════════════════════════

function drawTrendChart(values) {
  const canvas = document.getElementById('trend-chart');
  const ctx    = canvas.getContext('2d');

  // DPI scaling
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.parentElement.clientWidth - 16;
  const H = 70;
  canvas.width  = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width  = W + 'px';
  canvas.style.height = H + 'px';
  ctx.scale(dpr, dpr);

  if (!values || values.length < 2) {
    ctx.fillStyle = 'rgba(255,255,255,0.1)';
    ctx.font = '10px JetBrains Mono, monospace';
    ctx.fillText('Данные временного ряда недоступны', 8, H / 2);
    return;
  }

  ctx.clearRect(0, 0, W, H);

  const pad   = { t: 8, r: 8, b: 20, l: 8 };
  const inner = { w: W - pad.l - pad.r, h: H - pad.t - pad.b };
  const n     = values.length;

  const minV = Math.min(...values) - 0.05;
  const maxV = Math.max(...values) + 0.05;
  const rng  = maxV - minV || 1;

  const px = i => pad.l + (i / (n - 1)) * inner.w;
  const py = v => pad.t + inner.h - ((v - minV) / rng) * inner.h;

  // Grid lines
  ctx.strokeStyle = 'rgba(255,255,255,0.05)';
  ctx.lineWidth   = 1;
  [0, 0.5, 1].forEach(t => {
    const y = pad.t + t * inner.h;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
  });

  // Area fill
  const grad = ctx.createLinearGradient(0, pad.t, 0, H - pad.b);
  grad.addColorStop(0,   'rgba(0,255,135,0.25)');
  grad.addColorStop(1,   'rgba(0,255,135,0.0)');
  ctx.beginPath();
  ctx.moveTo(px(0), py(values[0]));
  values.forEach((v, i) => { if (i > 0) ctx.lineTo(px(i), py(v)); });
  ctx.lineTo(px(n - 1), H - pad.b);
  ctx.lineTo(px(0),     H - pad.b);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Line
  ctx.beginPath();
  ctx.strokeStyle = '#00FF87';
  ctx.lineWidth   = 1.5;
  ctx.lineJoin    = 'round';
  ctx.moveTo(px(0), py(values[0]));
  values.forEach((v, i) => { if (i > 0) ctx.lineTo(px(i), py(v)); });
  ctx.stroke();

  // Dots
  values.forEach((v, i) => {
    ctx.beginPath();
    ctx.arc(px(i), py(v), 2.5, 0, Math.PI * 2);
    ctx.fillStyle   = '#00FF87';
    ctx.fill();
  });

  // Year labels
  const years = ['2017','2018','2019','2020','2021','2022','2023'];
  const step  = Math.max(1, Math.floor(n / 4));
  ctx.fillStyle = 'rgba(148,163,184,0.7)';
  ctx.font      = '8px JetBrains Mono, monospace';
  ctx.textAlign = 'center';
  values.forEach((_, i) => {
    if (i % step === 0 || i === n - 1) {
      ctx.fillText(years[i] || '', px(i), H - 4);
    }
  });
}

// ════════════════════════════════════════════════════════════════
//   DEMO DATA (когда бэкенд недоступен)
// ════════════════════════════════════════════════════════════════

function getDemoPixelData(lat, lng) {
  // Генерируем правдоподобные данные на основе координат
  const seed  = (lat * 1000 + lng * 100) % 1;
  const ndvi  = parseFloat((0.1 + Math.abs(seed) * 0.6).toFixed(2));
  const ndwi  = parseFloat((-0.3 + Math.abs(seed) * 0.5).toFixed(2));

  const classes = ['Ирригированное поле', 'Пастбище', 'Голая почва', 'Пустыня', 'Населённый пункт'];
  const trends  = ['↑ +5%', '↓ −3%', '→ стабильно', '↑ +11%'];

  return {
    ndvi,
    ndwi,
    land_class:   classes[Math.floor(Math.abs(seed) * classes.length)],
    trend_label:  trends[Math.floor(Math.abs(seed) * trends.length)],
    trend:        getDemoTrend(),
    recommendations: [
      'Оценить доступность ирригации в данном районе',
      'Сравнить с данными 2020 года',
    ],
  };
}

function getDemoTrend() {
  return [0.28, 0.31, 0.27, 0.33, 0.35, 0.29, 0.34];
}

// ════════════════════════════════════════════════════════════════
//   BACKEND HEALTH CHECK
// ════════════════════════════════════════════════════════════════

async function fetchHealth() {
  try {
    const res = await fetch(`${CONFIG.API_BASE}/health`, { signal: AbortSignal.timeout(3000) });
    if (res.ok) {
      const data = await res.json();
      console.log('Backend OK:', data);
      // Можно обновить статус-пилл в topbar
    }
  } catch {
    console.warn('Backend недоступен — работаем с демо-данными');
    // Не фейлим — покажем демо
  }
}

function showLayerError(layerId) {
  console.error(`Слой ${layerId} недоступен`);
  // В будущем — toast уведомление
}

// ════════════════════════════════════════════════════════════════
//   HELPERS
// ════════════════════════════════════════════════════════════════

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ════════════════════════════════════════════════════════════════
//   INIT
// ════════════════════════════════════════════════════════════════
window.addEventListener('DOMContentLoaded', boot);
