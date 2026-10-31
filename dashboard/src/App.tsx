import { useEffect, useState } from 'react'
import { dayTime, useDemoNow } from './api'
import { getMode, setMode, type ThemeMode } from './Chart'
import { DetectionLog } from './DetectionLog'
import { LiveLots } from './LiveLots'
import { Patterns } from './Patterns'
import { UserPage } from './UserPage'

const TABS = { lots: 'Live lots', detection: 'Detection log', patterns: 'Booking patterns', user: 'User drill-down' } as const
type Tab = keyof typeof TABS

// Hash routes: #lots, #detection, #patterns, #user/U0001
function parseHash(): { tab: Tab; arg: string | null } {
  const [head, arg] = location.hash.slice(1).split('/')
  return { tab: head in TABS ? (head as Tab) : 'lots', arg: arg ? decodeURIComponent(arg) : null }
}

export default function App() {
  const [route, setRoute] = useState(parseHash)
  const [mode, setModeState] = useState<ThemeMode>(getMode)
  const now = useDemoNow()
  const [lastUser, setLastUser] = useState<string | null>(route.tab === 'user' ? route.arg : null)

  useEffect(() => {
    const onHash = () => {
      const r = parseHash()
      setRoute(r)
      if (r.tab === 'user' && r.arg) setLastUser(r.arg)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const select = (t: Tab) => { location.hash = t === 'user' && lastUser ? `user/${lastUser}` : t }
  const pickMode = (m: ThemeMode) => { setMode(m); setModeState(m) }

  return (
    <>
      <header className="top">
        <span className="brand"><span className="brand-mark" aria-hidden>P</span>GoPark Sense <small>operations</small></span>
        <nav aria-label="Pages">
          {(Object.keys(TABS) as Tab[]).map((t) => (
            <button key={t} className={t === route.tab ? 'active' : ''} aria-current={t === route.tab ? 'page' : undefined}
              onClick={() => select(t)}>{TABS[t]}</button>
          ))}
        </nav>
        <div className="top-right">
          <span className="clock" title="Demo clock from the API">Demo clock <b className="num">{now ? dayTime(now) : '—'}</b></span>
          <div className="segmented" role="group" aria-label="Theme">
            {(['light', 'system', 'dark'] as ThemeMode[]).map((m) => (
              <button key={m} className={m === mode ? 'on' : ''} aria-pressed={m === mode} onClick={() => pickMode(m)}>
                {m === 'system' ? 'Auto' : m[0].toUpperCase() + m.slice(1)}
              </button>
            ))}
          </div>
        </div>
      </header>
      {route.tab === 'lots' && <LiveLots />}
      {route.tab === 'detection' && <DetectionLog now={now} />}
      {route.tab === 'patterns' && <Patterns />}
      {route.tab === 'user' && <UserPage userId={route.arg} />}
    </>
  )
}
