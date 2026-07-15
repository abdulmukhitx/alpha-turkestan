import { useEffect, useMemo, useState } from 'react'
import { useI18n } from '../i18n.jsx'

export default function TimelapseControl({ open, periods, period, onPeriodChange }) {
  const { t, periodLabel } = useI18n()
  const [playing, setPlaying] = useState(false)
  const available = useMemo(() => periods.filter((item) => item.available !== false), [periods])
  const index = Math.max(0, available.findIndex((item) => item.period_id === period))

  useEffect(() => {
    if (!open || available.length < 2) setPlaying(false)
  }, [open, available.length])

  useEffect(() => {
    if (!playing || available.length < 2) return undefined
    const timer = window.setInterval(() => {
      const current = available.findIndex((item) => item.period_id === period)
      const next = available[(Math.max(0, current) + 1) % available.length]
      onPeriodChange(next.period_id)
    }, 1400)
    return () => window.clearInterval(timer)
  }, [playing, available, period, onPeriodChange])

  if (!open || available.length < 2) return null
  const current = available[index]
  return (
    <section className="timelapse-control" aria-label={t('timelapse.aria')}>
      <button type="button" className={playing ? 'playing' : ''} onClick={() => setPlaying((value) => !value)} aria-pressed={playing}>
        <span aria-hidden="true">{playing ? 'Ⅱ' : '▶'}</span>
        {playing ? t('timelapse.pause') : t('timelapse.play')}
      </button>
      <label>
        <span>{periodLabel(current)}</span>
        <input
          type="range"
          min="0"
          max={available.length - 1}
          step="1"
          value={index}
          onChange={(event) => {
            setPlaying(false)
            onPeriodChange(available[Number(event.target.value)].period_id)
          }}
          aria-label={t('timelapse.period')}
        />
      </label>
      <small>{available[0].period_id.slice(0, 4)}–{available.at(-1).period_id.slice(0, 4)}</small>
    </section>
  )
}
