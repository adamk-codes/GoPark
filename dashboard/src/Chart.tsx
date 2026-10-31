// Thin ECharts wrapper + theme tokens. Charts read colours from the CSS tokens in index.css,
// so light/dark come from one place and a theme switch repaints every chart.
import { useEffect, useMemo, useRef, useState } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, HeatmapChart, LineChart, ScatterChart } from 'echarts/charts'
import {
  DataZoomComponent, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent,
  TooltipComponent, VisualMapComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, HeatmapChart, LineChart, ScatterChart, DataZoomComponent, GridComponent, LegendComponent,
  MarkAreaComponent, MarkLineComponent, TooltipComponent, VisualMapComponent, CanvasRenderer])

export type Option = echarts.EChartsCoreOption
export type ThemeMode = 'system' | 'light' | 'dark'

const TOKEN_NAMES = ['fg', 'muted', 'faint', 'line', 'line-strong', 'surface', 'surface-2', 'accent', 'series-forecast',
  'series-actual', 'seq-0', 'seq-1', 'seq-2', 'seq-3', 'seq-4', 'good', 'warning', 'critical', 'info'] as const
export type Tokens = Record<(typeof TOKEN_NAMES)[number], string>
const THEME_EVENT = 'gopark-theme'

export function getMode(): ThemeMode {
  try { return (localStorage.getItem('gopark-theme') as ThemeMode) || 'system' } catch { return 'system' }
}

export function setMode(mode: ThemeMode) {
  if (mode === 'system') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = mode
  try { localStorage.setItem('gopark-theme', mode) } catch { /* private window */ }
  window.dispatchEvent(new Event(THEME_EVENT))
}

/** Current colour tokens; re-read whenever the theme or OS scheme changes. */
export function useTokens(): Tokens {
  const [version, setVersion] = useState(0)
  useEffect(() => {
    const bump = () => setVersion((v) => v + 1)
    const mq = matchMedia('(prefers-color-scheme: dark)')
    window.addEventListener(THEME_EVENT, bump)
    mq.addEventListener('change', bump)
    return () => { window.removeEventListener(THEME_EVENT, bump); mq.removeEventListener('change', bump) }
  }, [])
  return useMemo(() => {
    const css = getComputedStyle(document.documentElement)
    return Object.fromEntries(TOKEN_NAMES.map((n) => [n, css.getPropertyValue(`--${n}`).trim()])) as Tokens
  }, [version])
}

/** Shared axis/tooltip styling so every chart has recessive grid and token-coloured text. */
export function base(t: Tokens) {
  const axis = {
    axisLine: { lineStyle: { color: t['line-strong'] } },
    axisTick: { show: false },
    axisLabel: { color: t.muted, fontSize: 11 },
    splitLine: { lineStyle: { color: t.line, width: 1 } },
    nameTextStyle: { color: t.muted, fontSize: 11 },
  }
  const tooltip = {
    backgroundColor: t.surface, borderColor: t['line-strong'], borderWidth: 1, padding: [6, 10],
    textStyle: { color: t.fg, fontSize: 12 }, extraCssText: 'box-shadow: 0 4px 12px rgb(0 0 0 / .12); border-radius: 8px;',
  }
  return { axis, tooltip, textStyle: { fontFamily: 'Inter, system-ui, sans-serif', color: t.fg } }
}

export function Chart({ option, className = 'chart', onClick, label }: {
  option: Option; className?: string; label: string
  onClick?: (params: { seriesName?: string; data?: unknown; dataIndex: number }) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const inst = useRef<echarts.ECharts | null>(null)
  const click = useRef(onClick)
  useEffect(() => { click.current = onClick }, [onClick])

  useEffect(() => {
    const chart = echarts.init(ref.current!)
    inst.current = chart
    chart.on('click', (p) => click.current?.(p as never))
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(ref.current!)
    return () => { ro.disconnect(); chart.dispose(); inst.current = null }
  }, [])

  useEffect(() => { inst.current?.setOption(option, true) }, [option])

  return <div ref={ref} className={className} role="img" aria-label={label} />
}
