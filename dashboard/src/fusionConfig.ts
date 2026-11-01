// Fusion thresholds and priors, read from config/fusion.yaml at build time so the dashboard's guides
// always match what the API's fusion uses. Only flat numeric keys are needed, so a regex beats a YAML dependency.
import raw from '../../config/fusion.yaml?raw'

function num(key: string, fallback: number): number {
  const m = raw.match(new RegExp(`^\\s*${key}:\\s*([0-9.]+)`, 'm'))
  return m ? Number(m[1]) : fallback
}

export const FUSION = {
  autoConfirm: num('auto_confirm', 0.95),
  askDriver: num('ask_driver', 0.6),
  attendantAfterMin: num('attendant_after_min', 10),
  defaultPrior: num('default_prior', 0.3),
  halfLifeMin: num('freshness_half_life_min', 15),
  /** log_lr per signal source; definitive sources map to null. */
  logLr: Object.fromEntries(
    [...raw.matchAll(/^\s{2}(\w+):\s*\{(.*)\}/gm)].map(([, src, body]) => {
      const v = body.match(/log_lr:\s*(-?[0-9.]+)/)
      return [src, v ? Number(v[1]) : null]
    }),
  ) as Record<string, number | null>,
}
