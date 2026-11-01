import { useEffect, useState } from 'react'
import { dayTime, useDemoNow } from './api'
import { getMode, setMode, type ThemeMode } from './Chart'
import { DemoFeed } from './DemoFeed'
import { DetectionLog } from './DetectionLog'
import { Evaluation } from './Evaluation'
import { LiveLots } from './LiveLots'
import { Patterns } from './Patterns'
import { UserPage } from './UserPage'

const TABS = { lots: 'Live lots', detection: 'Detection log', patterns: 'Booking patterns', user: 'User drill-down', evaluation: 'Evaluation' } as const
type Tab = keyof typeof TABS

// Hash routes: #lots, #detection, #detection/AUB/S-B000123, #patterns, #user/U0001, #evaluation
function parseHash(): { tab: Tab; arg: string | null; arg2: string | null } {
  const [head, arg, arg2] = location.hash.slice(1).split('/')
  const dec = (s?: string) => (s ? decodeURIComponent(s) : null)
  return { tab: head in TABS ? (head as Tab) : 'lots', arg: dec(arg), arg2: dec(arg2) }
}

export default function App() {
  const [route, setRoute] = useState(parseHash)
  const [mode, setModeState] = useState<ThemeMode>(getMode)
  // Demo narration banner: on with ?demo (tools/demo.py opens that URL) or the header toggle; remembered per browser.
  const [demo, setDemo] = useState(() => {
    if (new URLSearchParams(location.search).has('demo')) return true
    try { return localStorage.getItem('gopark-demo') === '1' } catch { return false }
  })
  const toggleDemo = (on: boolean) => {
    setDemo(on)
    try { localStorage.setItem('gopark-demo', on ? '1' : '0') } catch { /* private window */ }
  }
  const now = useDemoNow(demo ? 2500 : 15000)
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
          <button className={`button small${demo ? ' primary' : ''}`} aria-pressed={demo} onClick={() => toggleDemo(!demo)}>Demo feed</button>
          <span className="clock" title="Demo clock from the API">Demo clock <b className="num">{now ? dayTime(now) : '—'}</b></span>
          <div className="segmented" role="group" aria-label="Theme">
            {(['light', 'system', 'dark'] as ThemeMode[]).map((m) => (
              <button key={m} className={m === mode ? 'on' : ''} aria-pressed={m === mode} onClick={() => pickMode(m)}>
                {m === 'system' ? 'Auto' : m[0].toUpperCase() + m.slice(1)}
              </button>
            ))}
          </div>
        </div>
        {demo && <DemoFeed onClose={() => toggleDemo(false)} />}
      </header>
      {route.tab === 'lots' && <LiveLots />}
      {route.tab === 'detection' && <DetectionLog now={now} lot={route.arg} stayId={route.arg2} />}
      {route.tab === 'patterns' && <Patterns />}
      {route.tab === 'user' && <UserPage userId={route.arg} />}
      {route.tab === 'evaluation' && <Evaluation />}
    </>
  )
}
