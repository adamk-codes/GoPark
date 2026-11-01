import { useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Badge, Button, Card, Muted, Section, s } from '../../components/ui';
import type { Alert } from '../../contract';
import type { ConflictItem } from '../../core/store';
import { hhmm } from '../../lib/format';
import { useSync } from '../../lib/sync';
import { t } from '../../strings';
import { c, toneColors, type Tone } from '../../theme';

// Fusion and sync flag these when a person should look at the car (brief P1 §P2).
const NEEDS_EYES = new Set<Alert['type']>(['low_confidence', 'anomaly']);
const severityTone: Record<Alert['severity'], Tone> = { info: 'info', warning: 'warn', critical: 'bad' };

export default function Alerts() {
  const engine = useSync();
  const [refreshing, setRefreshing] = useState(false);
  const refresh = async () => {
    setRefreshing(true);
    await engine.flush();
    await engine.pull();
    setRefreshing(false);
  };

  // From the local cache, so the list works offline; a resolve hides the alert at once and syncs later.
  const open = [...engine.view().alerts].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id);
  const eyes = open.filter((a) => NEEDS_EYES.has(a.type));
  const rest = open.filter((a) => !NEEDS_EYES.has(a.type));
  const conflicts = engine.conflictItems();
  const { lastPullAt } = engine.status();

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.body}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={c.brand} />}>
      {lastPullAt === null && <Muted>{t.common.loading}</Muted>}
      {conflicts.length > 0 && <Section title={t.sync.conflicts} />}
      {conflicts.map((cf) => <ConflictRow key={cf.id} cf={cf} onDismiss={() => engine.dismissConflict(cf.id)} />)}
      {lastPullAt !== null && open.length === 0 && conflicts.length === 0 && <Muted>{t.alerts.empty}</Muted>}
      {eyes.length > 0 && <Section title={t.alerts.needsEyes} />}
      {eyes.map((a) => <AlertRow key={a.id} a={a} onResolve={() => engine.resolveAlert(a.id)} />)}
      {rest.length > 0 && (eyes.length > 0 || conflicts.length > 0) && <Section title={t.alerts.other} />}
      {rest.map((a) => <AlertRow key={a.id} a={a} onResolve={() => engine.resolveAlert(a.id)} />)}
    </ScrollView>
  );
}

function AlertRow({ a, onResolve }: { a: Alert; onResolve: () => void }) {
  const tone = toneColors[severityTone[a.severity]];
  return (
    <Card style={[st.card, { borderStartColor: tone.fg }]}>
      <View style={s.row}>
        <Badge label={t.alerts.types[a.type] ?? a.type} fg={tone.fg} bg={tone.bg} />
        {a.estimated_fee != null && <Badge label={t.alerts.fee(a.estimated_fee)} fg={c.warn} bg={c.warnSoft} />}
        <View style={s.flex} />
        <Text style={s.mono}>{hhmm(a.created_at)}</Text>
      </View>
      <Text style={st.msg}>{a.message}</Text>
      {(a.booking_id || a.stay_id) && <Muted>{[a.booking_id, a.stay_id].filter(Boolean).join(' · ')}</Muted>}
      {a.type !== 'upcoming_arrival' && <Button kind="secondary" label={t.alerts.resolve} onPress={onResolve} />}
    </Card>
  );
}

// An offline action the server refused (e.g. a scan for a booking that was already checked out).
function ConflictRow({ cf, onDismiss }: { cf: ConflictItem; onDismiss: () => void }) {
  const { event } = cf;
  const { booking_id, plate, stay_id, alert_id } = event.body;
  const subject = booking_id ?? plate ?? stay_id ?? (alert_id != null ? `#${alert_id}` : null);
  return (
    <Card style={[st.card, { borderStartColor: c.bad }]}>
      <View style={s.row}>
        <Badge label={t.sync.conflict} fg={c.bad} bg={c.badSoft} />
        <Badge label={t.sync.eventTypes[event.type] ?? event.type} fg={c.muted} bg={c.border} />
        <View style={s.flex} />
        <Text style={s.mono}>{hhmm(event.observed_at)}</Text>
      </View>
      <Text style={st.msg}>{cf.reason}</Text>
      {subject != null && <Muted>{String(subject)}</Muted>}
      <Button kind="secondary" label={t.sync.dismiss} onPress={onDismiss} />
    </Card>
  );
}

const st = StyleSheet.create({
  card: { borderStartWidth: 5 },
  msg: { fontSize: 16, color: c.text, fontWeight: '600' },
});
