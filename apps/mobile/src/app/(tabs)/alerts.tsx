import { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Badge, Button, Card, ErrorLine, Muted, Section, s } from '../../components/ui';
import { errorText, hhmm } from '../../lib/format';
import { useApp } from '../../lib/settings';
import { useResource } from '../../lib/useResource';
import type { Alert } from '../../pending-contract';
import { t } from '../../strings';
import { c, toneColors, type Tone } from '../../theme';

const POLL_MS = 15_000;
const NEEDS_EYES = new Set<Alert['type']>(['low_confidence', 'anomaly', 'plate_mismatch', 'unconfirmed_departure']);
const severityTone: Record<Alert['severity'], Tone> = { info: 'info', warning: 'warn', critical: 'bad' };

// P0 alerts have no id yet (P2 adds them); fall back to a stable composite key.
const keyOf = (a: Alert) => a.id ?? `${a.type}|${a.stay_id ?? ''}|${a.booking_id ?? ''}`;

export default function Alerts() {
  const { api, settings } = useApp();
  const load = useCallback(() => api.alerts(settings.lotId), [api, settings.lotId]);
  const { data, error, refreshing, refresh } = useResource(load, `${settings.apiUrl}|${settings.lotId}`, POLL_MS);
  // Resolved on this phone; hides alerts until the server stops returning them.
  const [resolved, setResolved] = useState<Set<string>>(new Set());
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const resolve = async (a: Alert) => {
    const k = keyOf(a);
    setBusyKey(k);
    setActionError(null);
    try {
      await api.resolveAlert(a);
      setResolved((prev) => new Set(prev).add(k));
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setBusyKey(null);
    }
  };

  const open = (data ?? []).filter((a) => a.status !== 'resolved' && !resolved.has(keyOf(a)));
  const eyes = open.filter((a) => NEEDS_EYES.has(a.type));
  const rest = open.filter((a) => !NEEDS_EYES.has(a.type));

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.body}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={c.brand} />}>
      {error && <ErrorLine text={`${t.common.cantReach(settings.apiUrl)} · ${error}`} />}
      <ErrorLine text={actionError} />
      {!data && !error && <Muted>{t.common.loading}</Muted>}
      {data && open.length === 0 && <Muted>{t.alerts.empty}</Muted>}
      {eyes.length > 0 && <Section title={t.alerts.needsEyes} />}
      {eyes.map((a) => <AlertRow key={keyOf(a)} a={a} busy={busyKey === keyOf(a)} onResolve={() => resolve(a)} />)}
      {eyes.length > 0 && rest.length > 0 && <Section title={t.alerts.other} />}
      {rest.map((a) => <AlertRow key={keyOf(a)} a={a} busy={busyKey === keyOf(a)} onResolve={() => resolve(a)} />)}
    </ScrollView>
  );
}

function AlertRow({ a, busy, onResolve }: { a: Alert; busy: boolean; onResolve: () => void }) {
  const tone = toneColors[severityTone[a.severity]];
  return (
    <Card style={[st.card, { borderStartColor: tone.fg }]}>
      <View style={s.row}>
        <Badge label={t.alerts.types[a.type] ?? a.type} fg={tone.fg} bg={tone.bg} />
        {a.fee_usd != null && <Badge label={t.alerts.fee(a.fee_usd)} fg={c.warn} bg={c.warnSoft} />}
        <View style={s.flex} />
        <Text style={s.mono}>{hhmm(a.created_at)}</Text>
      </View>
      <Text style={st.msg}>{a.message}</Text>
      {a.type !== 'upcoming_arrival' && (
        <Button kind="secondary" label={t.alerts.resolve} busy={busy} onPress={onResolve} />
      )}
    </Card>
  );
}

const st = StyleSheet.create({
  card: { borderStartWidth: 5 },
  msg: { fontSize: 16, color: c.text, fontWeight: '600' },
});
