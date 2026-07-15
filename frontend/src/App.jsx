import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import TopBar from './components/TopBar.jsx'
import LayerPanel from './components/LayerPanel.jsx'
import MapView from './components/MapView.jsx'
import ChangeDetectionBar from './components/ChangeDetectionBar.jsx'
import ForecastBar from './components/ForecastBar.jsx'
import WorkspaceNav from './components/WorkspaceNav.jsx'
import AccountDialog from './components/AccountDialog.jsx'
import ZoneMigrationDialog from './components/ZoneMigrationDialog.jsx'
import TimelapseControl from './components/TimelapseControl.jsx'
import {
  changeAccountPassword, createAccountZone, createSavedAnalysis, deleteAccount, deleteAccountZone, deleteSavedAnalysis,
  fetchAccountAuthConfig, fetchAccountExport, fetchAccountSessions, fetchAccountZones, fetchSavedAnalyses,
  confirmEmailVerification, fetchAnalysis, fetchChangeStats, fetchCurrentAccount, fetchHealth, fetchMetadata, fetchPeriods,
  fetchPixel, fetchPointForecast, fetchTransect, fetchZoneStats, fetchZoneTimeSeries, importAccountZones,
  linkGoogleAccount, loginAccount, loginWithGoogle, logoutAccount, registerAccount, requestPasswordReset, resendEmailVerification, resetAccountPassword,
  revokeAccountSession, revokeOtherAccountSessions,
  updateAccountPreferences, updateAccountProfile, updateAccountZone,
} from './api'
import { clearSavedZones, cloneGeometry, newZoneId, readSavedZones, writeSavedZones } from './zoneStorage.js'
import { useI18n } from './i18n.jsx'
import { copyText } from './share.js'

const SplitMapView = lazy(() => import('./components/SplitMapView.jsx'))
const AnalysisPanel = lazy(() => import('./components/AnalysisPanel.jsx'))

const FALLBACK_CENTER = [43.39, 68.36]
const FALLBACK_ZOOM = 7

const BOOT_STEPS = [
  [80,  'boot.connect'],
  [260, 'boot.geodata'],
  [460, 'boot.map'],
  [640, 'boot.ready'],
]

export default function App() {
  const { locale, setLocale, t } = useI18n()
  const [booting, setBooting] = useState(true)
  const [bootFadeOut, setBootFadeOut] = useState(false)
  const [bootMsg, setBootMsg] = useState(BOOT_STEPS[0][1])
  const [bootPct, setBootPct] = useState(0)

  const [health, setHealth] = useState(null)
  const [meta, setMeta] = useState(null)
  const [periods, setPeriods] = useState([])
  const [period, setPeriod] = useState('2025_summer')
  const [activeLayer, setActiveLayer] = useState('ndvi')
  const [defaultBasemap, setDefaultBasemap] = useState('satellite')
  const [opacity, setOpacity] = useState(0.85)
  const [hoverPos, setHoverPos] = useState(null)
  const [leftPanelOpen, setLeftPanelOpen] = useState(true)
  const [rightPanelOpen, setRightPanelOpen] = useState(false)

  const [account, setAccount] = useState(null)
  const [accountAuthConfig, setAccountAuthConfig] = useState({ google: { enabled: false, client_id: null } })
  const [accountLoading, setAccountLoading] = useState(true)
  const [accountDialogOpen, setAccountDialogOpen] = useState(false)
  const [accountDialogView, setAccountDialogView] = useState('login')
  const [accountDialogNotice, setAccountDialogNotice] = useState('')
  const [accountDialogError, setAccountDialogError] = useState('')
  const [passwordResetToken, setPasswordResetToken] = useState('')
  const [analysisHistoryRevision, setAnalysisHistoryRevision] = useState(0)
  const [shareStatus, setShareStatus] = useState('')
  const [pendingLocalZones, setPendingLocalZones] = useState(null)
  const [migrationLoading, setMigrationLoading] = useState(false)
  const [migrationError, setMigrationError] = useState(null)

  const [point, setPixelPoint] = useState(null)
  const [pixel, setPixel] = useState(null)
  const [aiText, setAiText] = useState('')
  const [aiLoading, setAiLoading] = useState(false)
  const [aiError, setAiError] = useState(null)

  const [drawMode, setDrawMode] = useState(false)
  const [zonePolygon, setZonePolygon] = useState(null)
  const [zoneStats, setZoneStats] = useState(null)
  const [zoneLoading, setZoneLoading] = useState(false)
  const [zoneError, setZoneError] = useState(null)
  const [zoneTimeSeries, setZoneTimeSeries] = useState(null)
  const [zoneTimeSeriesLoading, setZoneTimeSeriesLoading] = useState(false)
  const [zoneTimeSeriesError, setZoneTimeSeriesError] = useState(null)
  const [zoneContext, setZoneContext] = useState({ period: '2025_summer', layer: 'ndvi', pane: 'main' })
  const [clearSignal, setClearSignal] = useState(0)
  const [finishSignal, setFinishSignal] = useState(0)
  const [drawPointCount, setDrawPointCount] = useState(0)
  const [savedZones, setSavedZones] = useState(readSavedZones)
  const [activeZoneId, setActiveZoneId] = useState(null)
  const [zoneDirty, setZoneDirty] = useState(false)
  const [zoneEditMode, setZoneEditMode] = useState(false)
  const [zoneStorageError, setZoneStorageError] = useState(null)
  const [zoneFocusSignal, setZoneFocusSignal] = useState(0)

  const [lineDrawMode, setLineDrawMode] = useState(false)
  const [transectLine, setTransectLine] = useState(null)
  const [transectData, setTransectData] = useState(null)
  const [transectLoading, setTransectLoading] = useState(false)
  const [transectError, setTransectError] = useState(null)
  const [transectContext, setTransectContext] = useState({ period: '2025_summer', layer: 'ndvi', pane: 'main' })
  const [lineClearSignal, setLineClearSignal] = useState(0)
  const [lineFinishSignal, setLineFinishSignal] = useState(0)
  const [lineDrawPointCount, setLineDrawPointCount] = useState(0)

  // Change detection — overlaid on top of the normal index layer (not instead
  // of it), toggled from a map-toolbar button. Reuses the same polygon-draw
  // plumbing as the regular zone tool (drawMode, clearSignal, finishSignal,
  // drawPointCount below); drawIntent decides which endpoint a just-finished
  // polygon feeds.
  const [changeMode, setChangeMode] = useState(false)
  const [changeIndex, setChangeIndex] = useState('ndvi')
  const [changePeriodBefore, setChangePeriodBefore] = useState(null)
  const [changePeriodAfter, setChangePeriodAfter] = useState(null)
  const [changeStats, setChangeStats] = useState(null)
  const [changeLoading, setChangeLoading] = useState(false)
  const [changeError, setChangeError] = useState(null)
  const [changePolygon, setChangePolygon] = useState(null)
  const [drawIntent, setDrawIntent] = useState('zone')

  // Split-screen comparison — mutually exclusive with change detection.
  // Left/right period+index default once periods load: earliest->NDVI, latest->NDVI.
  const [splitMode, setSplitMode] = useState(false)
  const [splitLeftPeriod, setSplitLeftPeriod] = useState(null)
  const [splitRightPeriod, setSplitRightPeriod] = useState(null)
  const [splitLeftIndex, setSplitLeftIndex] = useState('ndvi')
  const [splitRightIndex, setSplitRightIndex] = useState('ndvi')

  // Experimental baseline forecast. It is intentionally separate from the
  // current LULC classifier and can later be replaced by forecast COGs/model
  // endpoints without changing the UI contract.
  const [forecastMode, setForecastMode] = useState(false)
  const [forecastYear, setForecastYear] = useState(2026)
  const [forecastResult, setForecastResult] = useState(null)
  const [forecastLoading, setForecastLoading] = useState(false)
  const [forecastError, setForecastError] = useState(null)

  const requestIdRef = useRef(0)
  const zoneReqIdRef = useRef(0)
  const zoneTimeSeriesReqIdRef = useRef(0)
  const transectReqIdRef = useRef(0)
  const changeReqIdRef = useRef(0)
  const forecastReqIdRef = useRef(0)
  const deepLinkAppliedRef = useRef(false)

  useEffect(() => {
    const total = BOOT_STEPS[BOOT_STEPS.length - 1][0]
    const timers = BOOT_STEPS.map(([ms, msg]) => setTimeout(() => {
      setBootMsg(msg)
      setBootPct(Math.round((ms / total) * 100))
    }, ms))
    timers.push(setTimeout(() => setBootFadeOut(true), total + 150))
    timers.push(setTimeout(() => setBooting(false), total + 650))
    return () => timers.forEach(clearTimeout)
  }, [])

  useEffect(() => {
    refreshData()
    initializeAccount()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // default change-detection period pick: earliest -> latest, once periods load
  useEffect(() => {
    if (!periods.length || changePeriodBefore || changePeriodAfter) return
    setChangePeriodBefore(periods[0].period_id)
    setChangePeriodAfter(periods[periods.length - 1].period_id)
  }, [periods, changePeriodBefore, changePeriodAfter])

  // default split-screen period pick: earliest -> latest, once periods load
  useEffect(() => {
    if (!periods.length || splitLeftPeriod || splitRightPeriod) return
    setSplitLeftPeriod(periods[0].period_id)
    setSplitRightPeriod(periods[periods.length - 1].period_id)
  }, [periods, splitLeftPeriod, splitRightPeriod])

  useEffect(() => {
    const minYear = meta?.forecast?.min_target_year
    const maxYear = meta?.forecast?.max_target_year
    if (!minYear || !maxYear) return
    setForecastYear((year) => Math.max(minYear, Math.min(maxYear, year)))
  }, [meta])

  useEffect(() => {
    if (deepLinkAppliedRef.current || accountLoading || !meta || !periods.length) return
    const params = new URLSearchParams(window.location.search)
    const hasWorkspaceLink = ['account', 'analysis', 'zone', 'lat', 'lon', 'period', 'layer', 'mode']
      .some((key) => params.has(key))
    if (!hasWorkspaceLink) {
      deepLinkAppliedRef.current = true
      return
    }

    deepLinkAppliedRef.current = true
    const requestedPeriod = periods.some((item) => item.period_id === params.get('period'))
      ? params.get('period')
      : period
    const requestedLayer = params.get('layer') === 'satellite' || meta.layers?.[params.get('layer')]
      ? params.get('layer')
      : activeLayer
    const requestedMode = params.get('mode')

    setPeriod(requestedPeriod)
    setActiveLayer(requestedLayer)
    if (requestedMode === 'compare') setSplitMode(true)
    else if (requestedMode === 'change') {
      setChangeMode(true)
      if (periods.some((item) => item.period_id === params.get('before'))) setChangePeriodBefore(params.get('before'))
      if (periods.some((item) => item.period_id === params.get('after'))) setChangePeriodAfter(params.get('after'))
      if (meta.layers?.[params.get('change_index')]) setChangeIndex(params.get('change_index'))
    } else if (requestedMode === 'forecast' && meta.forecast?.enabled) {
      setForecastMode(true)
      const targetYear = Number(params.get('target_year'))
      if (Number.isInteger(targetYear)) setForecastYear(targetYear)
    }

    const accountView = params.get('account')
    if (accountView) {
      setAccountDialogView(accountView === 'profile' && account ? 'profile' : 'login')
      setAccountDialogOpen(true)
    }

    async function restoreLinkedState() {
      const analysisId = params.get('analysis')
      if (analysisId) {
        if (!account?.user?.email_verified) {
          setAccountDialogView(account ? 'profile' : 'login')
          setAccountDialogOpen(true)
          setAccountDialogNotice(t('history.signInHelp'))
          return
        }
        const result = await fetchSavedAnalyses()
        const analysis = result.analyses?.find((item) => item.id === analysisId)
        if (!analysis) throw new Error(t('error.analysisNotFound'))
        applySavedAnalysis(analysis)
        return
      }

      const zoneId = params.get('zone')
      if (zoneId) {
        const zone = savedZones.find((item) => item.id === zoneId)
        if (!zone) throw new Error(t('saved.notFound'))
        const geometry = cloneGeometry(zone.geometry)
        setZonePolygon(geometry)
        setActiveZoneId(zone.id)
        setZoneDirty(false)
        setZoneEditMode(false)
        setZoneFocusSignal((value) => value + 1)
        runZoneStats(geometry, { period: requestedPeriod, layer: requestedLayer, pane: 'main' })
        return
      }

      const lat = Number(params.get('lat'))
      const lon = Number(params.get('lon'))
      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        handlePointClick(lat, lon, { period: requestedPeriod, layer: requestedLayer, pane: 'main' })
      }
    }

    restoreLinkedState().catch((requestError) => {
      setZoneStorageError(requestError.message || t('common.error.server'))
    })
    // Deep links are intentionally applied once after account, metadata and zones finish loading.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountLoading, meta, periods, account, savedZones])

  function clearForecastPoint() {
    forecastReqIdRef.current += 1
    setForecastResult(null)
    setForecastError(null)
    setForecastLoading(false)
    setPixelPoint(null)
    setPixel(null)
    setAiText('')
    setAiError(null)
    setAiLoading(false)
  }

  function toggleSplitMode() {
    const next = !splitMode
    setSplitMode(next)
    if (next) {
      setChangeMode(false)
      setForecastMode(false)
      setDrawMode(false)
      setLineDrawMode(false)
      if (forecastMode) clearForecastPoint()
    }
  }

  function toggleChangeMode() {
    const next = !changeMode
    setChangeMode(next)
    if (next) {
      setSplitMode(false)
      setForecastMode(false)
      setLineDrawMode(false)
      if (forecastMode) clearForecastPoint()
    }
  }

  function toggleForecastMode() {
    if (!meta?.forecast?.enabled) return
    const next = !forecastMode
    setForecastMode(next)
    setSplitMode(false)
    setChangeMode(false)
    setDrawMode(false)
    setLineDrawMode(false)
    clearForecastPoint()
    if (next && activeLayer === 'satellite') setActiveLayer('ndvi')
  }

  function activateWorkspaceMode(nextMode) {
    if (nextMode === 'overview') {
      if (splitMode) toggleSplitMode()
      else if (changeMode) toggleChangeMode()
      else if (forecastMode) toggleForecastMode()
      return
    }
    if (nextMode === 'compare' && !splitMode) toggleSplitMode()
    if (nextMode === 'change' && !changeMode) toggleChangeMode()
    if (nextMode === 'forecast' && !forecastMode && meta?.forecast?.enabled) {
      toggleForecastMode()
      setRightPanelOpen(true)
    }
  }

  function refreshData() {
    fetchHealth().then(setHealth).catch(() => setHealth({ status: 'offline' }))
    refreshAccountAuthConfig().catch(() => {})
    fetchMetadata().then(setMeta).catch(() => setMeta(null))
    fetchPeriods()
      .then((items) => setPeriods(items.filter((item) => item.available !== false)))
      .catch(() => setPeriods([]))
  }

  async function refreshAccountAuthConfig() {
    const config = await fetchAccountAuthConfig()
    setAccountAuthConfig(config)
    return config
  }

  function applyAccountPreferences(preferences = {}) {
    if (preferences.locale) setLocale(preferences.locale)
    if (preferences.default_layer) setActiveLayer(preferences.default_layer)
    if (preferences.default_basemap) setDefaultBasemap(preferences.default_basemap)
    if (preferences.default_period) setPeriod(preferences.default_period)
    if (typeof preferences.default_opacity === 'number') setOpacity(preferences.default_opacity)
    if (typeof preferences.left_panel_open === 'boolean') setLeftPanelOpen(preferences.left_panel_open)
    if (typeof preferences.right_panel_open === 'boolean') setRightPanelOpen(preferences.right_panel_open)
  }

  async function activateAccountSession(session, { offerMigration = true } = {}) {
    const localZones = readSavedZones()
    setAccount(session)
    setActiveZoneId(null)
    setZoneDirty(false)
    setZoneStorageError(null)
    applyAccountPreferences(session.preferences)
    if (!session.user.email_verified) {
      setSavedZones(localZones)
      setPendingLocalZones(null)
      return session
    }
    const result = await fetchAccountZones()
    setSavedZones(result.zones || [])
    if (offerMigration && localZones.length) {
      setPendingLocalZones(localZones)
      setMigrationError(null)
    }
    return session
  }

  async function restoreAccount() {
    try {
      const session = await fetchCurrentAccount()
      if (session) await activateAccountSession(session)
    } catch (error) {
      setZoneStorageError(`${t('error.accountLoad')}: ${error.message || t('common.error.server')}`)
    } finally {
      setAccountLoading(false)
    }
  }

  function clearAccountLinkQuery() {
    const url = new URL(window.location.href)
    url.searchParams.delete('verify_email')
    url.searchParams.delete('reset_password')
    window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`)
  }

  async function initializeAccount() {
    const params = new URLSearchParams(window.location.search)
    const verificationToken = params.get('verify_email')
    const resetToken = params.get('reset_password')
    if (verificationToken) {
      try {
        const session = await confirmEmailVerification(verificationToken)
        await activateAccountSession(session, { offerMigration: false })
        setAccountDialogView('profile')
        setAccountDialogNotice(t('account.verifySuccess'))
        setAccountDialogError('')
        setAccountDialogOpen(true)
      } catch (error) {
        setAccountDialogView('login')
        setAccountDialogNotice('')
        setAccountDialogError(error.code ? t(error.code) : (error.message || t('error.verification')))
        setAccountDialogOpen(true)
      } finally {
        clearAccountLinkQuery()
        setAccountLoading(false)
      }
      return
    }
    if (resetToken) {
      setPasswordResetToken(resetToken)
      setAccountDialogView('reset')
      setAccountDialogOpen(true)
      clearAccountLinkQuery()
      setAccountLoading(false)
      return
    }
    await restoreAccount()
  }

  async function handleLogin(credentials) {
    const session = await loginAccount(credentials)
    await activateAccountSession(session)
    setAccountDialogOpen(false)
    return session
  }

  async function handleRegister(details) {
    const session = await registerAccount(details)
    await activateAccountSession(session)
    return session
  }

  async function handleGoogleLogin(credential) {
    const session = await loginWithGoogle({ credential, locale })
    await activateAccountSession(session)
    if (session.user.email_verified) setAccountDialogOpen(false)
    return session
  }

  async function handleGoogleLink(credential) {
    const session = await linkGoogleAccount({ credential, locale })
    setAccount(session)
    return session
  }

  async function handleSaveProfile(displayName, preferences) {
    await updateAccountProfile(displayName)
    const session = await updateAccountPreferences(preferences)
    setAccount(session)
    applyAccountPreferences(session.preferences)
    return session
  }

  async function handleLocaleChange(nextLocale) {
    if (!account) return
    try {
      const session = await updateAccountPreferences({ ...account.preferences, locale: nextLocale })
      setAccount(session)
    } catch (error) {
      setZoneStorageError(error.message || t('error.profileSave'))
    }
  }

  async function handleForgotPassword(details) {
    return requestPasswordReset(details)
  }

  async function handleResetPassword(details) {
    const session = await resetAccountPassword(details)
    setPasswordResetToken('')
    setAccountDialogNotice(t('account.passwordReset'))
    await activateAccountSession(session, { offerMigration: false })
    return session
  }

  async function handleResendVerification() {
    return resendEmailVerification()
  }

  async function handleAlertRulesChange(thresholdAlerts) {
    if (!account) return null
    const session = await updateAccountPreferences({
      ...account.preferences,
      threshold_alerts: thresholdAlerts,
    })
    setAccount(session)
    return session
  }

  async function handleSaveAnalysis() {
    let analysis = null
    if (forecastMode && forecastResult) {
      analysis = {
        kind: 'forecast',
        title: t('history.forecastTitle', { index: activeLayer.toUpperCase(), year: forecastYear }),
        payload: { point, index: activeLayer, target_year: forecastYear, result: forecastResult },
      }
    } else if (changeStats) {
      analysis = {
        kind: 'change',
        title: t('history.changeTitle', { before: changePeriodBefore.slice(0, 4), after: changePeriodAfter.slice(0, 4) }),
        payload: {
          geometry: changePolygon,
          index: changeIndex,
          period_before: changePeriodBefore,
          period_after: changePeriodAfter,
          result: changeStats,
        },
      }
    } else if (zoneStats) {
      analysis = {
        kind: 'zone',
        title: activeSavedZone?.name || t('history.zoneTitle'),
        payload: {
          geometry: zonePolygon,
          index: zoneContext.layer,
          period: zoneContext.period,
          result: zoneStats,
          time_series: zoneTimeSeries,
        },
      }
    } else if (transectData) {
      analysis = {
        kind: 'transect',
        title: t('history.transectTitle', { index: activeLayer.toUpperCase() }),
        payload: { geometry: transectLine, index: activeLayer, period, result: transectData },
      }
    } else if (pixel) {
      analysis = {
        kind: 'point',
        title: t('history.pointTitle', { index: activeLayer.toUpperCase(), period: period.slice(0, 4) }),
        payload: { point, index: activeLayer, period, result: pixel, interpretation: aiText },
      }
    }
    if (!analysis) throw new Error(t('history.nothingToSave'))
    const saved = await createSavedAnalysis(analysis)
    setAnalysisHistoryRevision((value) => value + 1)
    return saved
  }

  function applySavedAnalysis(analysis) {
    const payload = analysis.payload || {}
    setSplitMode(false)
    setChangeMode(false)
    setForecastMode(false)
    setDrawMode(false)
    setLineDrawMode(false)
    setRightPanelOpen(true)

    if (analysis.kind === 'point') {
      if (payload.period) setPeriod(payload.period)
      if (payload.index) setActiveLayer(payload.index)
      setPixelPoint(payload.point || null)
      setPixel(payload.result || null)
      setAiText(payload.interpretation || '')
      setAiError(null)
      return
    }

    if (analysis.kind === 'zone') {
      if (payload.period) setPeriod(payload.period)
      if (payload.index) setActiveLayer(payload.index)
      setZonePolygon(payload.geometry || null)
      setZoneStats(payload.result || null)
      setZoneTimeSeries(payload.time_series || null)
      setZoneContext({ period: payload.period || period, layer: payload.index || activeLayer, pane: 'main' })
      setActiveZoneId(null)
      setZoneDirty(false)
      setZoneFocusSignal((value) => value + 1)
      return
    }

    if (analysis.kind === 'change') {
      setChangeMode(true)
      setChangePolygon(payload.geometry || null)
      setChangeStats(payload.result || null)
      if (payload.index) setChangeIndex(payload.index)
      if (payload.period_before) setChangePeriodBefore(payload.period_before)
      if (payload.period_after) setChangePeriodAfter(payload.period_after)
      return
    }

    if (analysis.kind === 'forecast') {
      setForecastMode(true)
      setPixelPoint(payload.point || null)
      setForecastResult(payload.result || null)
      if (payload.index) setActiveLayer(payload.index)
      if (payload.target_year) setForecastYear(payload.target_year)
      return
    }

    if (analysis.kind === 'transect') {
      if (payload.period) setPeriod(payload.period)
      if (payload.index) setActiveLayer(payload.index)
      setTransectLine(payload.geometry || null)
      setTransectData(payload.result || null)
      setTransectContext({ period: payload.period || period, layer: payload.index || activeLayer, pane: 'main' })
    }
  }

  async function handleShareWorkspace() {
    const url = new URL('/map', window.location.origin)
    url.searchParams.set('period', period)
    url.searchParams.set('layer', activeLayer)
    if (splitMode) url.searchParams.set('mode', 'compare')
    else if (changeMode) {
      url.searchParams.set('mode', 'change')
      url.searchParams.set('before', changePeriodBefore || '')
      url.searchParams.set('after', changePeriodAfter || '')
      url.searchParams.set('change_index', changeIndex)
    } else if (forecastMode) {
      url.searchParams.set('mode', 'forecast')
      url.searchParams.set('target_year', String(forecastYear))
    }
    if (activeZoneId) url.searchParams.set('zone', activeZoneId)
    else if (point?.lat != null && point?.lng != null) {
      url.searchParams.set('lat', Number(point.lat).toFixed(6))
      url.searchParams.set('lon', Number(point.lng).toFixed(6))
    }
    try {
      await copyText(url.toString())
      setShareStatus(t('nav.linkCopied'))
      window.setTimeout(() => setShareStatus(''), 1800)
    } catch (error) {
      setZoneStorageError(error.message || t('common.error.server'))
    }
  }

  async function handleRevokeAccountSession(sessionId) {
    const result = await revokeAccountSession(sessionId)
    if (result.current) {
      setAccount(null)
      setSavedZones(readSavedZones())
      setActiveZoneId(null)
      setZoneDirty(false)
      setPendingLocalZones(null)
      setMigrationError(null)
      setAccountDialogOpen(false)
    }
    return result
  }

  async function handleLogout() {
    await logoutAccount()
    setAccount(null)
    setSavedZones(readSavedZones())
    setActiveZoneId(null)
    setZoneDirty(false)
    setPendingLocalZones(null)
    setMigrationError(null)
    setAccountDialogOpen(false)
  }

  async function handleDeleteAccount(password) {
    await deleteAccount(password)
    setAccount(null)
    setSavedZones(readSavedZones())
    setActiveZoneId(null)
    setZoneDirty(false)
    setPendingLocalZones(null)
    setAccountDialogOpen(false)
  }

  async function handleImportLocalZones() {
    if (!pendingLocalZones?.length) return
    setMigrationLoading(true)
    setMigrationError(null)
    try {
      const result = await importAccountZones(pendingLocalZones)
      setSavedZones(result.zones || [])
      clearSavedZones()
      setPendingLocalZones(null)
    } catch (error) {
      setMigrationError(error.message || t('error.migration'))
    } finally {
      setMigrationLoading(false)
    }
  }

  function openAccountDialog() {
    refreshAccountAuthConfig().catch(() => {})
    setAccountDialogView(account ? 'profile' : 'login')
    setAccountDialogNotice('')
    setAccountDialogError('')
    setAccountDialogOpen(true)
  }

  async function runForecastPoint(lat, lng, index = activeLayer, targetYear = forecastYear) {
    const forecastIndex = index === 'satellite' ? 'ndvi' : index
    setPixelPoint({ lat, lng, period: `forecast_${targetYear}`, layer: forecastIndex, pane: 'main' })
    setForecastResult(null)
    setForecastError(null)
    setForecastLoading(true)

    const reqId = ++forecastReqIdRef.current
    try {
      const result = await fetchPointForecast(lat, lng, forecastIndex, targetYear)
      if (forecastReqIdRef.current !== reqId) return
      setForecastResult(result)
    } catch (e) {
      if (forecastReqIdRef.current !== reqId) return
      setForecastError(e.message || t('error.forecast'))
    } finally {
      if (forecastReqIdRef.current === reqId) setForecastLoading(false)
    }
  }

  async function handlePointClick(lat, lng, context = {}) {
    const target = {
      period: context.period || period,
      layer: context.layer || activeLayer,
      pane: context.pane || 'main',
    }
    setRightPanelOpen(true)
    if (forecastMode && target.pane === 'main') {
      runForecastPoint(lat, lng, target.layer, forecastYear)
      return
    }
    setPixelPoint({ lat, lng, ...target })
    setPixel(null)
    setAiText('')
    setAiError(null)
    setAiLoading(true)

    const reqId = ++requestIdRef.current
    let px
    try {
      px = await fetchPixel(lat, lng, target.period)
      if (requestIdRef.current !== reqId) return
      setPixel(px)
    } catch (e) {
      if (requestIdRef.current !== reqId) return
      setAiError(e.message || t('error.pixel'))
      setAiLoading(false)
      return
    }

    if (px.demo) {
      setAiError(t('analysis.demo'))
      setAiLoading(false)
      return
    }

    try {
      const analysis = await fetchAnalysis({
        lat, lon: lng, period: target.period,
        ndvi: px.ndvi, ndwi: px.ndwi, ndre: px.ndre, ndmi: px.ndmi, bsi: px.bsi, savi: px.savi, nbr: px.nbr,
        ml_class: px.ml_class, ml_class_ru: px.ml_class_ru, ml_confidence: px.ml_confidence, locale,
      })
      if (requestIdRef.current !== reqId) return
      setAiText(analysis.analysis || t('error.analysisUnavailable'))
    } catch (e) {
      if (requestIdRef.current !== reqId) return
      setAiError(t('error.aiUnavailable', { message: e.message || t('common.error.server') }))
    } finally {
      if (requestIdRef.current === reqId) setAiLoading(false)
    }
  }

  async function runZoneTimeSeries(geometry) {
    setZoneTimeSeries(null)
    setZoneTimeSeriesError(null)
    setZoneTimeSeriesLoading(true)
    const reqId = ++zoneTimeSeriesReqIdRef.current
    try {
      const result = await fetchZoneTimeSeries(geometry)
      if (zoneTimeSeriesReqIdRef.current !== reqId) return
      setZoneTimeSeries(result)
    } catch (e) {
      if (zoneTimeSeriesReqIdRef.current !== reqId) return
      setZoneTimeSeriesError(e.message || t('error.timeseries'))
    } finally {
      if (zoneTimeSeriesReqIdRef.current === reqId) setZoneTimeSeriesLoading(false)
    }
  }

  async function runZoneStats(geometry, context = {}) {
    const target = {
      period: context.period || period,
      layer: context.layer || activeLayer,
      pane: context.pane || 'main',
    }
    if (context.refreshTimeSeries !== false) runZoneTimeSeries(geometry)
    setRightPanelOpen(true)
    setZoneContext(target)
    setZoneStats(null)
    setZoneError(null)
    setZoneLoading(true)

    const reqId = ++zoneReqIdRef.current
    try {
      const stats = await fetchZoneStats(geometry, target.period)
      if (zoneReqIdRef.current !== reqId) return
      setZoneStats(stats)
    } catch (e) {
      if (zoneReqIdRef.current !== reqId) return
      setZoneError(e.message || t('error.zoneStats'))
    } finally {
      if (zoneReqIdRef.current === reqId) setZoneLoading(false)
    }
  }

  function handlePolygonDrawn(geometry, context = {}) {
    setDrawMode(false)
    if (drawIntent === 'change') {
      zoneReqIdRef.current += 1
      zoneTimeSeriesReqIdRef.current += 1
      setZonePolygon(null)
      setZoneStats(null)
      setZoneError(null)
      setZoneTimeSeries(null)
      setZoneTimeSeriesError(null)
      setZoneTimeSeriesLoading(false)
      setActiveZoneId(null)
      setZoneDirty(false)
      setZoneEditMode(false)
      setChangePolygon(geometry)
      runChangeStats(geometry, changePeriodBefore, changePeriodAfter)
    } else {
      changeReqIdRef.current += 1
      setChangePolygon(null)
      setChangeStats(null)
      setChangeError(null)
      setZonePolygon(geometry)
      setActiveZoneId(null)
      setZoneDirty(true)
      setZoneEditMode(false)
      runZoneStats(geometry, context)
    }
  }

  function handleClearZone() {
    zoneReqIdRef.current += 1   // invalidate any in-flight request
    zoneTimeSeriesReqIdRef.current += 1
    setZonePolygon(null)
    setZoneStats(null)
    setZoneError(null)
    setZoneLoading(false)
    setZoneTimeSeries(null)
    setZoneTimeSeriesError(null)
    setZoneTimeSeriesLoading(false)
    setActiveZoneId(null)
    setZoneDirty(false)
    setZoneEditMode(false)
    setDrawMode(false)
    setDrawPointCount(0)
    setClearSignal((n) => n + 1)
  }

  function commitSavedZones(next) {
    try {
      writeSavedZones(next)
      setSavedZones(next)
      setZoneStorageError(null)
      return true
    } catch {
      setZoneStorageError(t('error.localStorage'))
      return false
    }
  }

  async function handleSaveZone(name) {
    if (!zonePolygon) return false
    const now = new Date().toISOString()
    const zone = {
      id: newZoneId(),
      name,
      geometry: cloneGeometry(zonePolygon),
      createdAt: now,
      updatedAt: now,
    }
    if (account?.user?.email_verified) {
      try {
        const saved = await createAccountZone(zone)
        setSavedZones((current) => [saved, ...current])
        setActiveZoneId(saved.id)
        setZoneDirty(false)
        setZoneStorageError(null)
        return true
      } catch (error) {
        setZoneStorageError(t('error.saveZone', { message: error.message || t('common.error.server') }))
        return false
      }
    }
    if (!commitSavedZones([...savedZones, zone])) return false
    setActiveZoneId(zone.id)
    setZoneDirty(false)
    return true
  }

  async function handleUpdateZone() {
    if (!zonePolygon || !activeZoneId) return false
    if (account?.user?.email_verified) {
      try {
        const updated = await updateAccountZone(activeZoneId, { geometry: cloneGeometry(zonePolygon) })
        setSavedZones((current) => current.map((zone) => zone.id === activeZoneId ? updated : zone))
        setZoneDirty(false)
        setZoneStorageError(null)
        return true
      } catch (error) {
        setZoneStorageError(t('error.updateZone', { message: error.message || t('common.error.server') }))
        return false
      }
    }
    const next = savedZones.map((zone) => zone.id === activeZoneId
      ? { ...zone, geometry: cloneGeometry(zonePolygon), updatedAt: new Date().toISOString() }
      : zone)
    if (!commitSavedZones(next)) return false
    setZoneDirty(false)
    return true
  }

  function handleOpenZone(id) {
    const zone = savedZones.find((item) => item.id === id)
    if (!zone) return
    const geometry = cloneGeometry(zone.geometry)
    setSplitMode(false)
    setChangeMode(false)
    setForecastMode(false)
    setDrawMode(false)
    setLineDrawMode(false)
    setDrawIntent('zone')
    setChangePolygon(null)
    setChangeStats(null)
    setChangeError(null)
    setZonePolygon(geometry)
    setActiveZoneId(id)
    setZoneDirty(false)
    setZoneEditMode(false)
    setZoneFocusSignal((value) => value + 1)
    runZoneStats(geometry, { period, layer: activeLayer, pane: 'main' })
  }

  async function handleRenameZone(id, name) {
    if (account?.user?.email_verified) {
      try {
        const updated = await updateAccountZone(id, { name })
        setSavedZones((current) => current.map((zone) => zone.id === id ? updated : zone))
        setZoneStorageError(null)
        return true
      } catch (error) {
        setZoneStorageError(t('error.renameZone', { message: error.message || t('common.error.server') }))
        return false
      }
    }
    const next = savedZones.map((zone) => zone.id === id
      ? { ...zone, name, updatedAt: new Date().toISOString() }
      : zone)
    return commitSavedZones(next)
  }

  async function handleDeleteZone(id) {
    const zone = savedZones.find((item) => item.id === id)
    if (!zone || !window.confirm(t('saved.deleteConfirm', { name: zone.name }))) return false
    if (account?.user?.email_verified) {
      try {
        await deleteAccountZone(id)
        setSavedZones((current) => current.filter((item) => item.id !== id))
        setZoneStorageError(null)
        if (id === activeZoneId) handleClearZone()
        return true
      } catch (error) {
        setZoneStorageError(t('error.deleteZone', { message: error.message || t('common.error.server') }))
        return false
      }
    }
    const next = savedZones.filter((item) => item.id !== id)
    if (!commitSavedZones(next)) return false
    if (id === activeZoneId) handleClearZone()
    return true
  }

  function handleZoneEdited(geometry) {
    setZonePolygon(geometry)
    setZoneDirty(true)
    runZoneStats(geometry, { period, layer: activeLayer, pane: 'main' })
  }

  function toggleZoneEdit() {
    if (!zonePolygon) return
    setDrawMode(false)
    setLineDrawMode(false)
    setZoneEditMode((editing) => !editing)
  }

  function handleClearChange() {
    changeReqIdRef.current += 1
    setChangePolygon(null)
    setChangeStats(null)
    setChangeError(null)
    setChangeLoading(false)
    setDrawMode(false)
    setDrawPointCount(0)
    setClearSignal((n) => n + 1)
  }

  function toggleZoneDraw() {
    setDrawMode((d) => {
      if (!d) {
        setDrawIntent('zone')
        setLineDrawMode(false)
        setZoneEditMode(false)
      }
      return !d
    })
  }

  function toggleChangeDraw() {
    setDrawMode((d) => {
      if (!d) {
        setDrawIntent('change')
        setLineDrawMode(false)
      }
      return !d
    })
  }

  async function runChangeStats(geometry, periodBefore = changePeriodBefore, periodAfter = changePeriodAfter) {
    setRightPanelOpen(true)
    if (!periodBefore || !periodAfter || periodBefore === periodAfter) {
      setChangeStats(null)
      setChangeError(periodBefore === periodAfter ? t('change.chooseDifferent') : null)
      setChangeLoading(false)
      return
    }
    setChangeStats(null)
    setChangeError(null)
    setChangeLoading(true)

    const reqId = ++changeReqIdRef.current
    try {
      const stats = await fetchChangeStats(geometry, periodBefore, periodAfter, locale)
      if (changeReqIdRef.current !== reqId) return
      setChangeStats(stats)
    } catch (e) {
      if (changeReqIdRef.current !== reqId) return
      setChangeError(e.message || t('error.changeStats'))
    } finally {
      if (changeReqIdRef.current === reqId) setChangeLoading(false)
    }
  }

  async function runTransect(geometry, context = {}) {
    const target = {
      period: context.period || period,
      layer: context.layer || activeLayer,
      pane: context.pane || 'main',
    }
    setRightPanelOpen(true)
    setTransectContext(target)
    setTransectData(null)
    setTransectError(null)
    if (target.layer === 'satellite') {
      setTransectError(t('error.profileSatellite'))
      setTransectLoading(false)
      return
    }
    setTransectLoading(true)

    const reqId = ++transectReqIdRef.current
    try {
      const data = await fetchTransect(geometry, target.layer, target.period)
      if (transectReqIdRef.current !== reqId) return
      setTransectData(data)
    } catch (e) {
      if (transectReqIdRef.current !== reqId) return
      setTransectError(e.message || t('error.transect'))
    } finally {
      if (transectReqIdRef.current === reqId) setTransectLoading(false)
    }
  }

  function handleLineDrawn(geometry, context = {}) {
    setLineDrawMode(false)
    setTransectLine(geometry)
    runTransect(geometry, context)
  }

  function toggleLineDraw() {
    if (activeLayer === 'satellite') {
      setTransectError(t('error.profileIndex'))
      return
    }
    setLineDrawMode((drawing) => {
      if (!drawing) setDrawMode(false)
      return !drawing
    })
  }

  function handleClearLine() {
    transectReqIdRef.current += 1
    setTransectLine(null)
    setTransectData(null)
    setTransectError(null)
    setTransectLoading(false)
    setLineDrawMode(false)
    setLineDrawPointCount(0)
    setLineClearSignal((n) => n + 1)
  }

  // re-fetch the transect when the active layer changes while a line is already drawn
  useEffect(() => {
    if (!transectLine || splitMode || forecastMode) return
    runTransect(transectLine, { period, layer: activeLayer, pane: 'main' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeLayer])

  // switching period keeps any drawn geometry as-is and just re-queries it —
  // each period is viewed independently, no comparison between them
  useEffect(() => {
    if (splitMode || forecastMode) return
    if (point?.pane === 'main') handlePointClick(point.lat, point.lng, { period, layer: activeLayer, pane: 'main' })
    if (transectLine) runTransect(transectLine, { period, layer: activeLayer, pane: 'main' })
    if (zonePolygon) runZoneStats(zonePolygon, { period, layer: activeLayer, pane: 'main', refreshTimeSeries: false })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period])

  useEffect(() => {
    if (!forecastMode || !point || point.pane !== 'main') return
    runForecastPoint(point.lat, point.lng, activeLayer, forecastYear)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [forecastYear, activeLayer])

  useEffect(() => {
    if (!changePolygon) return
    runChangeStats(changePolygon, changePeriodBefore, changePeriodAfter)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [changePeriodBefore, changePeriodAfter])

  useEffect(() => {
    if (!splitMode || !splitLeftPeriod) return
    const context = { period: splitLeftPeriod, layer: splitLeftIndex, pane: 'left', refreshTimeSeries: false }
    if (point?.pane === 'left') handlePointClick(point.lat, point.lng, context)
    if (zonePolygon && zoneContext.pane === 'left') runZoneStats(zonePolygon, context)
    if (transectLine && transectContext.pane === 'left') runTransect(transectLine, context)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [splitLeftPeriod])

  useEffect(() => {
    if (!splitMode || !transectLine || transectContext.pane !== 'left') return
    runTransect(transectLine, { period: splitLeftPeriod, layer: splitLeftIndex, pane: 'left' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [splitLeftIndex])

  useEffect(() => {
    if (!splitMode || !splitRightPeriod || point?.pane !== 'right') return
    handlePointClick(point.lat, point.lng, { period: splitRightPeriod, layer: splitRightIndex, pane: 'right' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [splitRightPeriod])

  useEffect(() => {
    if (splitMode) return
    const context = { period, layer: activeLayer, pane: 'main', refreshTimeSeries: false }
    if (point && point.pane !== 'main') handlePointClick(point.lat, point.lng, context)
    if (zonePolygon && zoneContext.pane !== 'main') runZoneStats(zonePolygon, context)
    if (transectLine && transectContext.pane !== 'main') runTransect(transectLine, context)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [splitMode])

  const layers = {}
  if (meta?.layers) {
    for (const [id, cfg] of Object.entries(meta.layers)) {
      layers[id] = { ...cfg, cssGradient: meta.cmaps?.[id] }
    }
  }

  const activeSavedZone = savedZones.find((zone) => zone.id === activeZoneId) || null

  const workspaceMode = splitMode ? 'compare' : changeMode ? 'change' : forecastMode ? 'forecast' : 'overview'
  const hasResults = !!(
    point || pixel || aiText || aiError || aiLoading ||
    zoneStats || zoneLoading || zoneError || zoneTimeSeries || zoneTimeSeriesLoading || zoneTimeSeriesError ||
    transectData || transectLoading || transectError ||
    changeStats || changeLoading || changeError ||
    forecastResult || forecastLoading || forecastError
  )
  const showLeftPanel = !splitMode && leftPanelOpen
  const showRightPanel = rightPanelOpen
  const workspaceClass = [
    'workspace',
    !showLeftPanel && 'workspace-no-left',
    !showRightPanel && 'workspace-no-right',
    !showLeftPanel && !showRightPanel && 'workspace-map-only',
  ].filter(Boolean).join(' ')
  const shellInsets = {
    '--active-left-inset': showLeftPanel ? 'var(--panel-left-w)' : '0px',
    '--active-right-inset': showRightPanel ? 'var(--panel-right-w)' : '0px',
  }

  return (
    <div className="app-shell" style={shellInsets}>
      <a className="skip-link" href="#map-workspace">{t('app.skipMap')}</a>
      {booting && (
        <div id="boot-screen" className={bootFadeOut ? 'fade-out' : ''}>
          <div className="boot-inner">
            <div className="boot-title">GeoAI Platform</div>
            <div className="boot-sub">{t('brand.region')}</div>
            <div className="boot-bar"><div className="boot-fill" style={{ width: `${bootPct}%` }} /></div>
            <div className="boot-status">{t(bootMsg)}</div>
          </div>
        </div>
      )}

      <TopBar
        lat={hoverPos?.lat}
        lon={hoverPos?.lng}
        health={health}
        onRefresh={refreshData}
        periods={periods}
        period={period}
        onPeriodChange={setPeriod}
        periodDisabled={forecastMode}
        account={account}
        authConfig={accountAuthConfig}
        accountLoading={accountLoading}
        onAccountOpen={openAccountDialog}
        onLocaleChange={handleLocaleChange}
      />

      <WorkspaceNav
        activeMode={workspaceMode}
        onModeChange={activateWorkspaceMode}
        forecastAvailable={meta?.forecast?.enabled === true}
        leftPanelAvailable={!splitMode}
        leftPanelOpen={showLeftPanel}
        onToggleLeftPanel={() => setLeftPanelOpen((open) => !open)}
        rightPanelOpen={showRightPanel}
        onToggleRightPanel={() => setRightPanelOpen((open) => !open)}
        hasResults={hasResults}
        onShare={handleShareWorkspace}
        shareStatus={shareStatus}
      />

      <main className={workspaceClass}>
        {showLeftPanel && (
          <LayerPanel
            layers={layers}
            activeLayer={activeLayer}
            onSelect={setActiveLayer}
            opacity={opacity}
            onOpacityChange={setOpacity}
            drawMode={drawMode}
            onToggleDraw={toggleZoneDraw}
            onClearZone={handleClearZone}
            onFinishDraw={() => setFinishSignal((n) => n + 1)}
            hasZone={!!zonePolygon}
            drawPointCount={drawPointCount}
            savedZones={savedZones}
            activeZoneId={activeZoneId}
            zoneDirty={zoneDirty}
            zoneEditMode={zoneEditMode}
            zoneStorageError={zoneStorageError}
            savedZonesCloudMode={account?.user?.email_verified === true}
            onSaveZone={handleSaveZone}
            onUpdateZone={handleUpdateZone}
            onOpenZone={handleOpenZone}
            onRenameZone={handleRenameZone}
            onDeleteZone={handleDeleteZone}
            onToggleZoneEdit={toggleZoneEdit}
            lineDrawMode={lineDrawMode}
            onToggleLineDraw={toggleLineDraw}
            lineDisabled={activeLayer === 'satellite'}
            onClearLine={handleClearLine}
            onFinishLineDraw={() => setLineFinishSignal((n) => n + 1)}
            hasLine={!!transectLine}
            lineDrawPointCount={lineDrawPointCount}
            forecastMode={forecastMode}
            onClose={() => setLeftPanelOpen(false)}
          />
        )}

        {splitMode ? (
          <Suspense fallback={<div className="map-lazy-loading">{t('boot.map')}</div>}>
          <SplitMapView
            periods={periods}
            bounds={meta?.region?.bounds}
            center={meta?.region?.center || FALLBACK_CENTER}
            zoom={FALLBACK_ZOOM}
            initialBasemap={defaultBasemap}
            leftPeriod={splitLeftPeriod}
            leftIndex={splitLeftIndex}
            onLeftPeriodChange={setSplitLeftPeriod}
            onLeftIndexChange={setSplitLeftIndex}
            rightPeriod={splitRightPeriod}
            rightIndex={splitRightIndex}
            onRightPeriodChange={setSplitRightPeriod}
            onRightIndexChange={setSplitRightIndex}
            drawMode={drawMode}
            onPolygonDrawn={handlePolygonDrawn}
            clearSignal={clearSignal}
            finishSignal={finishSignal}
            onDrawPointsChange={setDrawPointCount}
            lineDrawMode={lineDrawMode}
            onLineDrawn={handleLineDrawn}
            lineClearSignal={lineClearSignal}
            lineFinishSignal={lineFinishSignal}
            onLineDrawPointsChange={setLineDrawPointCount}
            onPointClick={handlePointClick}
            onMouseMove={(lat, lng) => setHoverPos({ lat, lng })}
            onExitSplitMode={toggleSplitMode}
          />
          </Suspense>
        ) : (
          <MapView
            activeLayer={activeLayer}
            period={period}
            periods={periods}
            opacity={opacity}
            bounds={meta?.region?.bounds}
            center={meta?.region?.center || FALLBACK_CENTER}
            zoom={FALLBACK_ZOOM}
            initialBasemap={defaultBasemap}
            onPointClick={handlePointClick}
            onMouseMove={(lat, lng) => setHoverPos({ lat, lng })}
            onZoomChange={() => {}}
            drawMode={drawMode}
            onPolygonDrawn={handlePolygonDrawn}
            clearSignal={clearSignal}
            finishSignal={finishSignal}
            onDrawPointsChange={setDrawPointCount}
            zoneGeometry={zonePolygon}
            zoneEditMode={zoneEditMode}
            onPolygonEdited={handleZoneEdited}
            zoneFocusSignal={zoneFocusSignal}
            lineDrawMode={lineDrawMode}
            onLineDrawn={handleLineDrawn}
            lineClearSignal={lineClearSignal}
            lineFinishSignal={lineFinishSignal}
            onLineDrawPointsChange={setLineDrawPointCount}
            changeMode={changeMode}
            changeIndex={changeIndex}
            changePeriodBefore={changePeriodBefore}
            changePeriodAfter={changePeriodAfter}
            forecastMode={forecastMode}
            forecastYear={forecastYear}
          />
        )}

        {showRightPanel && (
          <Suspense fallback={<aside className="panel panel-right map-lazy-loading">{t('boot.map')}</aside>}>
          <AnalysisPanel
            point={point}
            pixel={pixel}
            aiText={aiText}
            loading={aiLoading}
            error={aiError}
            zoneStats={zoneStats}
            zoneLoading={zoneLoading}
            zoneError={zoneError}
            zoneGeometry={zonePolygon}
            activeLayer={zoneContext.layer}
            zonePeriod={zoneContext.period}
            zoneName={activeSavedZone?.name || t('common.unnamed')}
            zoneTimeSeries={zoneTimeSeries}
            zoneTimeSeriesLoading={zoneTimeSeriesLoading}
            zoneTimeSeriesError={zoneTimeSeriesError}
            transectData={transectData}
            transectLoading={transectLoading}
            transectError={transectError}
            changeStats={changeMode && !splitMode ? changeStats : null}
            changeLoading={changeMode && !splitMode ? changeLoading : false}
            changeError={changeMode && !splitMode ? changeError : null}
            forecastMode={forecastMode && !splitMode}
            forecastResult={forecastResult}
            forecastLoading={forecastLoading}
            forecastError={forecastError}
            forecastYear={forecastYear}
            forecastIndex={activeLayer}
            alertRules={account?.preferences?.threshold_alerts || []}
            onAlertRulesChange={handleAlertRulesChange}
            alertsCloudMode={account?.user?.email_verified === true}
            canSaveAnalysis={account?.user?.email_verified === true}
            onSaveAnalysis={handleSaveAnalysis}
            onClose={() => setRightPanelOpen(false)}
          />
          </Suspense>
        )}
      </main>

      <TimelapseControl
        open={!splitMode && !changeMode && !forecastMode}
        periods={periods}
        period={period}
        onPeriodChange={setPeriod}
      />

      <ChangeDetectionBar
        open={changeMode && !splitMode}
        periods={periods}
        periodBefore={changePeriodBefore}
        periodAfter={changePeriodAfter}
        onPeriodBeforeChange={setChangePeriodBefore}
        onPeriodAfterChange={setChangePeriodAfter}
        index={changeIndex}
        onIndexChange={setChangeIndex}
        drawMode={drawMode}
        onToggleDraw={toggleChangeDraw}
        onFinishDraw={() => setFinishSignal((n) => n + 1)}
        onClearZone={handleClearChange}
        hasZone={!!changePolygon}
        drawPointCount={drawPointCount}
      />

      <ForecastBar
        open={forecastMode && !splitMode}
        config={meta?.forecast}
        targetYear={forecastYear}
        onTargetYearChange={setForecastYear}
        activeIndex={activeLayer}
      />

      <AccountDialog
        open={accountDialogOpen}
        initialView={accountDialogView}
        initialNotice={accountDialogNotice}
        initialError={accountDialogError}
        resetToken={passwordResetToken}
        account={account}
        authConfig={accountAuthConfig}
        periods={periods}
        onClose={() => { setAccountDialogOpen(false); setAccountDialogNotice(''); setAccountDialogError('') }}
        onLogin={handleLogin}
        onRegister={handleRegister}
        onGoogleLogin={handleGoogleLogin}
        onGoogleLink={handleGoogleLink}
        onRefreshAuthConfig={refreshAccountAuthConfig}
        onSaveProfile={handleSaveProfile}
        onLogout={handleLogout}
        onExport={fetchAccountExport}
        onDeleteAccount={handleDeleteAccount}
        onForgotPassword={handleForgotPassword}
        onResetPassword={handleResetPassword}
        onResendVerification={handleResendVerification}
        onChangePassword={changeAccountPassword}
        onFetchSessions={fetchAccountSessions}
        onRevokeSession={handleRevokeAccountSession}
        onRevokeOtherSessions={revokeOtherAccountSessions}
        analysisRefreshKey={analysisHistoryRevision}
        onFetchAnalyses={fetchSavedAnalyses}
        onDeleteAnalysis={deleteSavedAnalysis}
      />

      <ZoneMigrationDialog
        zones={pendingLocalZones}
        loading={migrationLoading}
        error={migrationError}
        onImport={handleImportLocalZones}
        onSkip={() => { setPendingLocalZones(null); setMigrationError(null) }}
      />
    </div>
  )
}
