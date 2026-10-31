import type { Method, ScanResultCode } from './contract';

export const c = {
  bg: '#f4f7f5',
  card: '#ffffff',
  border: '#d9e3dd',
  text: '#14211b',
  muted: '#56655d',
  brand: '#178a54',
  brandSoft: '#e3f3ea',
  ok: '#178a54',
  okSoft: '#dff3e7',
  warn: '#b45d00',
  warnSoft: '#fdf0dc',
  bad: '#c0262d',
  badSoft: '#fbe3e3',
  info: '#245fa8',
  infoSoft: '#e2ecf8',
  free: '#dff3e7',
  reserved: '#fdf0dc',
  occupied: '#f3d0d0',
};

export type Tone = 'ok' | 'warn' | 'bad' | 'info';

export const resultTone: Record<ScanResultCode, Tone> = {
  checked_in: 'ok',
  checked_out: 'ok',
  wrong_lot: 'warn',
  too_early: 'warn',
  expired: 'warn',
  already_in: 'warn',
  not_found: 'bad',
  invalid_qr: 'bad',
};

export const toneColors: Record<Tone, { fg: string; bg: string }> = {
  ok: { fg: c.ok, bg: c.okSoft },
  warn: { fg: c.warn, bg: c.warnSoft },
  bad: { fg: c.bad, bg: c.badSoft },
  info: { fg: c.info, bg: c.infoSoft },
};

export const methodColors: Record<Method, { fg: string; bg: string }> = {
  auto: { fg: c.info, bg: c.infoSoft },
  driver: { fg: '#6b3fa0', bg: '#efe6f8' },
  attendant: { fg: c.ok, bg: c.okSoft },
  inferred: { fg: c.warn, bg: c.warnSoft },
};
