import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router'

const MapWorkspace = lazy(() => import('./App.jsx'))
const DashboardPage = lazy(() => import('./pages/DashboardPage.jsx'))
const HistoryPage = lazy(() => import('./pages/HistoryPage.jsx'))

function IndexRedirect() {
  const location = useLocation()
  return <Navigate replace to={{ pathname: '/map', search: location.search, hash: location.hash }} />
}

function PageLoading() {
  return (
    <div className="route-loading" role="status">
      <div className="boot-title">GeoAI·TKO</div>
      <div className="boot-bar"><div className="boot-fill" style={{ width: '72%' }} /></div>
    </div>
  )
}

export default function RootRoutes() {
  return (
    <Suspense fallback={<PageLoading />}>
      <Routes>
        <Route index element={<IndexRedirect />} />
        <Route path="/map" element={<MapWorkspace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="*" element={<Navigate replace to="/dashboard" />} />
      </Routes>
    </Suspense>
  )
}
