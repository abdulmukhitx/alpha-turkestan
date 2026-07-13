/* All requests go through the Vite dev proxy → http://localhost:8000 */

const ACCOUNT_MUTATION_HEADERS = {
  'Content-Type': 'application/json',
  'X-Requested-With': 'GeoAI-TKO',
}

async function accountRequest(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options })
  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    const message = Array.isArray(detail?.detail)
      ? detail.detail.map((item) => item.msg).filter(Boolean).join('. ')
      : (detail?.detail?.message || detail?.detail)
    let code = null
    if (response.status === 429) code = 'error.tooManyRequests'
    else if (path.endsWith('/register') && response.status === 409) code = 'error.duplicateEmail'
    else if (path.endsWith('/login') && response.status === 401) code = 'error.invalidCredentials'
    else if (path.endsWith('/verification/confirm') && response.status === 400) code = 'error.invalidVerificationLink'
    else if (path.endsWith('/password/reset') && response.status === 400) code = 'error.invalidResetLink'
    else if (path === '/api/account' && response.status === 401) code = 'error.wrongPassword'
    else if (response.status === 403) code = 'error.securityCheck'
    const error = new Error(message || `Account request failed: ${response.status}`)
    error.code = code
    throw error
  }
  if (response.status === 204) return null
  return response.json()
}

export async function fetchCurrentAccount() {
  const response = await fetch('/api/account/me', { credentials: 'same-origin' })
  if (response.status === 401) return null
  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    throw new Error(detail?.detail || `Account request failed: ${response.status}`)
  }
  return response.json()
}

export const registerAccount = (details) => accountRequest('/api/account/register', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify(details),
})

export const loginAccount = (details) => accountRequest('/api/account/login', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify(details),
})

export const resendEmailVerification = () => accountRequest('/api/account/verification/resend', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS,
})

export const confirmEmailVerification = (token) => accountRequest('/api/account/verification/confirm', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify({ token }),
})

export const requestPasswordReset = (details) => accountRequest('/api/account/password/forgot', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify(details),
})

export const resetAccountPassword = (details) => accountRequest('/api/account/password/reset', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify(details),
})

export const logoutAccount = () => accountRequest('/api/account/logout', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS,
})

export const updateAccountProfile = (displayName) => accountRequest('/api/account/profile', {
  method: 'PATCH', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify({ display_name: displayName }),
})

export const updateAccountPreferences = (preferences) => accountRequest('/api/account/preferences', {
  method: 'PUT', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify(preferences),
})

export const fetchAccountExport = () => accountRequest('/api/account/export')

export const deleteAccount = (password) => accountRequest('/api/account', {
  method: 'DELETE', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify({ password }),
})

export const fetchAccountZones = () => accountRequest('/api/account/zones')

export const createAccountZone = (zone) => accountRequest('/api/account/zones', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify(zone),
})

export const updateAccountZone = (id, changes) => accountRequest(`/api/account/zones/${encodeURIComponent(id)}`, {
  method: 'PATCH', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify(changes),
})

export const deleteAccountZone = (id) => accountRequest(`/api/account/zones/${encodeURIComponent(id)}`, {
  method: 'DELETE', headers: ACCOUNT_MUTATION_HEADERS,
})

export const importAccountZones = (zones) => accountRequest('/api/account/zones/import', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify({ zones }),
})

export async function fetchHealth() {
  const r = await fetch('/health')
  if (!r.ok) throw new Error(`Health fetch failed: ${r.status}`)
  return r.json()
}

export async function fetchMetadata() {
  const r = await fetch('/metadata')
  if (!r.ok) throw new Error(`Metadata fetch failed: ${r.status}`)
  return r.json()
}

export async function fetchPeriods() {
  const r = await fetch('/api/periods')
  if (!r.ok) throw new Error(`Periods fetch failed: ${r.status}`)
  return r.json()
}

export async function fetchPixel(lat, lon, period) {
  const r = await fetch(`/api/pixel?lat=${lat.toFixed(6)}&lon=${lon.toFixed(6)}&period=${period}`)
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Pixel fetch failed: ${r.status}`)
  }
  return r.json()
}

export async function fetchAnalysis({ lat, lon, period, ndvi, ndwi, ndre, ndmi, bsi, savi, nbr, ml_class, ml_class_ru, ml_confidence, locale = 'ru' }) {
  const r = await fetch('/api/analyze', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ lat, lon, period, ndvi, ndwi, ndre, ndmi, bsi, savi, nbr, ml_class, ml_class_ru, ml_confidence, locale }),
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Analysis failed: ${r.status}`)
  }
  return r.json()
}

/** Leaflet-compatible XYZ tile URL template for a given layer + period. */
export const tileUrl = (layer, period) => `/tiles/${layer}/{z}/{x}/{y}.png?period=${period}`

/** Experimental all-years linear-trend forecast tile URL. */
export const forecastTileUrl = (index, targetYear) =>
  `/tiles/forecast/${index}/${targetYear}/{z}/{x}/{y}.png`

export async function fetchPointForecast(lat, lon, index, targetYear) {
  const params = new URLSearchParams({
    lat: lat.toFixed(6),
    lon: lon.toFixed(6),
    index,
    target_year: String(targetYear),
  })
  const r = await fetch(`/api/forecast/point?${params}`)
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Forecast failed: ${r.status}`)
  }
  return r.json()
}

export async function fetchZoneStats(geometry, period) {
  const r = await fetch('/api/zone_stats', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ geometry, period }),
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Zone stats failed: ${r.status}`)
  }
  return r.json()
}

export async function fetchZoneTimeSeries(geometry) {
  const r = await fetch('/api/zone_timeseries', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ geometry }),
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Zone time series failed: ${r.status}`)
  }
  return r.json()
}

export async function fetchTransect(geometry, layer, period) {
  const r = await fetch('/api/transect', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ geometry, layer, period }),
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Transect failed: ${r.status}`)
  }
  return r.json()
}

/** Leaflet-compatible XYZ tile URL template for a change-detection layer. */
export const changeTileUrl = (index, periodBefore, periodAfter) =>
  `/tiles/change/${index}/{z}/{x}/{y}.png?period_before=${periodBefore}&period_after=${periodAfter}`

export async function fetchChangeStats(geometry, periodBefore, periodAfter, locale = 'ru') {
  const r = await fetch('/api/change_stats', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ geometry, period_before: periodBefore, period_after: periodAfter, locale }),
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Change stats failed: ${r.status}`)
  }
  return r.json()
}

export async function fetchZoneReport({ geometry, zoneStats, activeLayer, period, locale = 'ru' }) {
  const r = await fetch('/api/zone_report', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({
      geometry,
      zone_stats:       zoneStats,
      active_layer:     activeLayer,
      period,
      locale,
    }),
  })
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Zone report failed: ${r.status}`)
  }
  return r.json()
}
