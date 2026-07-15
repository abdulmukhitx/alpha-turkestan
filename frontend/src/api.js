/*
 * Local development leaves VITE_API_BASE_URL empty and uses Vite's proxy.
 * Production points it at the public FastAPI origin (for example,
 * https://api.geo-tko.online).
 */
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').trim().replace(/\/+$/, '')

export const apiUrl = (path) => `${API_BASE_URL}${path}`

const API_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 30_000)

const apiFetch = async (path, options = {}) => {
  const { signal, timeoutMs = API_TIMEOUT_MS, ...fetchOptions } = options
  const controller = new AbortController()
  const abortFromCaller = () => controller.abort(signal?.reason)
  if (signal?.aborted) abortFromCaller()
  else signal?.addEventListener('abort', abortFromCaller, { once: true })
  const timer = setTimeout(() => controller.abort(new Error('Request timed out')), timeoutMs)
  try {
    return await fetch(apiUrl(path), { ...fetchOptions, signal: controller.signal })
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abortFromCaller)
  }
}

const ACCOUNT_MUTATION_HEADERS = {
  'Content-Type': 'application/json',
  'X-Requested-With': 'GeoAI-TKO',
}

async function accountRequest(path, options = {}) {
  const response = await apiFetch(path, { credentials: 'include', ...options })
  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    const message = Array.isArray(detail?.detail)
      ? detail.detail.map((item) => item.msg).filter(Boolean).join('. ')
      : (detail?.detail?.message || detail?.detail)
    const providerErrorCodes = {
      google_token_invalid: 'error.googleInvalid',
      google_link_required: 'error.googleLinkRequired',
      google_identity_conflict: 'error.googleIdentityConflict',
      google_not_configured: 'error.googleUnavailable',
      google_unavailable: 'error.googleUnavailable',
      email_verification_required: 'error.emailVerificationRequired',
    }
    let code = providerErrorCodes[detail?.detail?.code] || null
    if (response.status === 429) code = 'error.tooManyRequests'
    else if (path.endsWith('/register') && response.status === 409) code = 'error.duplicateEmail'
    else if (path === '/api/account/login' && response.status === 401) code = 'error.invalidCredentials'
    else if (path.endsWith('/verification/confirm') && response.status === 400) code = 'error.invalidVerificationLink'
    else if (path.endsWith('/password/reset') && response.status === 400) code = 'error.invalidResetLink'
    else if (path.endsWith('/password/change') && response.status === 401) code = 'error.wrongPassword'
    else if (path.endsWith('/password/change') && response.status === 400) code = 'error.passwordUnchanged'
    else if (path.endsWith('/password/change') && response.status === 422) code = 'error.passwordPolicy'
    else if (path.includes('/sessions/') && response.status === 404) code = 'error.sessionNotFound'
    else if (path.includes('/analyses/') && response.status === 404) code = 'error.analysisNotFound'
    else if (path === '/api/account' && response.status === 401) code = 'error.wrongPassword'
    else if (response.status === 403 && !code) code = 'error.securityCheck'
    const error = new Error(message || `Account request failed: ${response.status}`)
    error.code = code
    throw error
  }
  if (response.status === 204) return null
  return response.json()
}

export async function fetchCurrentAccount() {
  const response = await apiFetch('/api/account/me', { credentials: 'include' })
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

export const fetchAccountAuthConfig = () => accountRequest('/api/account/auth/config')

export const loginWithGoogle = (details) => accountRequest('/api/account/google/login', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify(details),
})

export const linkGoogleAccount = (details) => accountRequest('/api/account/google/link', {
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

export const changeAccountPassword = (details) => accountRequest('/api/account/password/change', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify(details),
})

export const fetchAccountSessions = () => accountRequest('/api/account/sessions')

export const revokeAccountSession = (sessionId) => accountRequest(`/api/account/sessions/${encodeURIComponent(sessionId)}`, {
  method: 'DELETE', headers: ACCOUNT_MUTATION_HEADERS,
})

export const revokeOtherAccountSessions = () => accountRequest('/api/account/sessions/revoke-others', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS,
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

export const deleteAccount = (password = null) => accountRequest('/api/account', {
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

export const fetchSavedAnalyses = () => accountRequest('/api/account/analyses')

export const createSavedAnalysis = (analysis) => accountRequest('/api/account/analyses', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS, body: JSON.stringify(analysis),
})

export const deleteSavedAnalysis = (analysisId) => accountRequest(`/api/account/analyses/${encodeURIComponent(analysisId)}`, {
  method: 'DELETE', headers: ACCOUNT_MUTATION_HEADERS,
})

export const fetchMonitoringStatus = () => accountRequest('/api/account/monitoring/status')

export const runAccountMonitoring = () => accountRequest('/api/account/monitoring/run', {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS,
})

export const fetchAlertEvents = () => accountRequest('/api/account/alerts')

export const acknowledgeAlertEvent = (alertId) => accountRequest(`/api/account/alerts/${encodeURIComponent(alertId)}/acknowledge`, {
  method: 'POST', headers: ACCOUNT_MUTATION_HEADERS,
})

export async function fetchHealth() {
  const r = await apiFetch('/health')
  if (!r.ok) throw new Error(`Health fetch failed: ${r.status}`)
  return r.json()
}

export async function fetchMetadata() {
  const r = await apiFetch('/metadata')
  if (!r.ok) throw new Error(`Metadata fetch failed: ${r.status}`)
  return r.json()
}

export async function fetchPeriods() {
  const r = await apiFetch('/api/periods')
  if (!r.ok) throw new Error(`Periods fetch failed: ${r.status}`)
  return r.json()
}

export async function fetchPixel(lat, lon, period) {
  const r = await apiFetch(`/api/pixel?lat=${lat.toFixed(6)}&lon=${lon.toFixed(6)}&period=${period}`)
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Pixel fetch failed: ${r.status}`)
  }
  return r.json()
}

export async function fetchAnalysis({ lat, lon, period, ndvi, ndwi, ndre, ndmi, bsi, savi, nbr, ml_class, ml_class_ru, ml_confidence, locale = 'ru' }) {
  const r = await apiFetch('/api/analyze', {
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

/** Leaflet-compatible XYZ tile URL. Versioning makes immutable COG updates cache-safe. */
export const tileUrl = (layer, period, dataVersion = '') => {
  const version = dataVersion ? `&v=${encodeURIComponent(dataVersion)}` : ''
  return apiUrl(`/tiles/${layer}/{z}/{x}/{y}.png?period=${encodeURIComponent(period)}${version}`)
}

/** Experimental all-years linear-trend forecast tile URL. */
export const forecastTileUrl = (index, targetYear) =>
  apiUrl(`/tiles/forecast/${index}/${targetYear}/{z}/{x}/{y}.png`)

export async function fetchPointForecast(lat, lon, index, targetYear) {
  const params = new URLSearchParams({
    lat: lat.toFixed(6),
    lon: lon.toFixed(6),
    index,
    target_year: String(targetYear),
  })
  const r = await apiFetch(`/api/forecast/point?${params}`)
  if (!r.ok) {
    const detail = await r.json().catch(() => null)
    throw new Error(detail?.detail || `Forecast failed: ${r.status}`)
  }
  return r.json()
}

export async function fetchZoneStats(geometry, period) {
  const r = await apiFetch('/api/zone_stats', {
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
  const r = await apiFetch('/api/zone_timeseries', {
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
  const r = await apiFetch('/api/transect', {
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
  apiUrl(`/tiles/change/${index}/{z}/{x}/{y}.png?period_before=${periodBefore}&period_after=${periodAfter}`)

export async function fetchChangeStats(geometry, periodBefore, periodAfter, locale = 'ru') {
  const r = await apiFetch('/api/change_stats', {
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
  const r = await apiFetch('/api/zone_report', {
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
