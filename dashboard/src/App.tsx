import { useEffect, useState } from 'react'
import { LiveLots } from './LiveLots'
import { Patterns } from './Patterns'

const TABS = { lots: 'Live lots', patterns: 'Booking patterns' } as const
type Tab = keyof typeof TABS
const fromHash = (): Tab => (location.hash.slice(1) in TABS ? (location.hash.slice(1) as Tab) : 'lots')

export default function App() {
  const [tab, setTab] = useState<Tab>(fromHash)
  useEffect(() => {
    const onHash = () => setTab(fromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  const select = (t: Tab) => { location.hash = t }

  return (
    <>
      <header className="top">
        <strong className="brand">GoPark Sense</strong>
        <nav>
          {(Object.keys(TABS) as Tab[]).map((t) => (
            <button key={t} className={t === tab ? 'active' : ''} onClick={() => select(t)}>{TABS[t]}</button>
          ))}
        </nav>
      </header>
      {tab === 'lots' ? <LiveLots /> : <Patterns />}
    </>
  )
}
