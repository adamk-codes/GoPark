import { useCallback } from 'react';
import { FlatList, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { Badge, ErrorLine, Muted, s } from '../../components/ui';
import type { AuditEntry, Method, Stay, SyncEvent } from '../../contract';
import { hhmm } from '../../lib/format';
import { useApp } from '../../lib/settings';
import { useSync } from '../../lib/sync';
import { useResource } from '../../lib/useResource';
import { t } from '../../strings';
import { c, methodColors } from '../../theme';

const POLL_MS = 15_000;
const METHODS = new Set<Method>(['auto', 'driver', 'attendant', 'inferred']);

// How the change happened: the stay's in/out method when the entry carries a stay, else who acted.
function methodOf(e: AuditEntry): Method {
  const after = (e.after ?? null) as Partial<Stay> | null;
  const m = e.action === 'check_out' ? after?.out_method : after?.in_method;
  if (m && METHODS.has(m)) return m;
  return e.actor_type === 'driver' ? 'driver' : e.actor_type === 'system' ? 'auto' : 'attendant';
}

function plateOf(e: AuditEntry): string {
  const after = (e.after ?? null) as Partial<Stay> | null;
  return after?.plate_norm ?? e.entity_id;
}

// One History row: a server audit entry, or an action of this phone's still in the outbox or refused.
type Item = { kind: 'audit'; e: AuditEntry } | { kind: 'pending' | 'conflict'; ev: SyncEvent; title: string; sub: string };

export default function History() {
  const { api, settings } = useApp();
  const engine = useSync();
  const load = useCallback(() => api.audit(settings.lotId, 100), [api, settings.lotId]);
  const { data, error, refreshing, refresh } = useResource(load, `${settings.apiUrl}|${settings.lotId}`, POLL_MS,
    `audit:${settings.lotId}`);

  const local: Item[] = [
    ...engine.pendingItems().map((p) => ({ kind: 'pending' as const, ev: p.event,
      title: p.local ? `${t.result[p.local.result]} · ${p.local.booking?.plate ?? p.local.stay?.plate_norm ?? ''}`
        : t.sync.eventTypes[p.event.type], sub: p.local?.stay?.id ?? String(p.event.body.alert_id ?? '') })),
    ...engine.conflictItems().map((cf) => ({ kind: 'conflict' as const, ev: cf.event,
      title: t.sync.eventTypes[cf.event.type] ?? cf.event.type, sub: cf.reason })),
  ].reverse();
  const items: Item[] = [...local, ...(data ?? []).map((e) => ({ kind: 'audit' as const, e }))];

  return (
    <View style={s.screen}>
      <FlatList
        data={items}
        keyExtractor={(i) => (i.kind === 'audit' ? String(i.e.id) : i.ev.client_event_id)}
        contentContainerStyle={s.body}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={c.brand} />}
        ListHeaderComponent={<ErrorLine text={error && `${t.common.cantReach(settings.apiUrl)} · ${error}`} />}
        ListEmptyComponent={<Muted>{data ? t.history.empty : t.common.loading}</Muted>}
        renderItem={({ item }) => (item.kind === 'audit' ? <Row e={item.e} /> : <LocalRow item={item} />)}
      />
    </View>
  );
}

function Row({ e }: { e: AuditEntry }) {
  const m = methodOf(e);
  return (
    <View style={st.row}>
      <Text style={[s.mono, st.time]}>{hhmm(e.observed_at)}</Text>
      <View style={s.flex}>
        <Text style={st.title}>{t.history.actions[e.action] ?? e.action} · {plateOf(e)}</Text>
        <Text style={st.sub}>{e.entity_id}{e.device_id ? ` · ${t.history.by(e.device_id)}` : ''}</Text>
      </View>
      <View style={st.badges}>
        <Badge label={t.history.methods[m]} {...methodColors[m]} />
        {/* Read from the server, so synced; the time is when it happened (offline actions keep theirs). */}
        <Badge label={t.history.synced} fg={c.ok} bg={c.okSoft} />
      </View>
    </View>
  );
}

function LocalRow({ item }: { item: Exclude<Item, { kind: 'audit' }> }) {
  const conflict = item.kind === 'conflict';
  return (
    <View style={[st.row, { borderColor: conflict ? c.bad : c.warn }]}>
      <Text style={[s.mono, st.time]}>{hhmm(item.ev.observed_at)}</Text>
      <View style={s.flex}>
        <Text style={st.title}>{item.title}</Text>
        {item.sub ? <Text style={st.sub}>{item.sub}</Text> : null}
      </View>
      <View style={st.badges}>
        <Badge label={t.history.methods.attendant} {...methodColors.attendant} />
        <Badge label={conflict ? t.sync.conflict : t.history.pending}
          fg={conflict ? c.bad : c.warn} bg={conflict ? c.badSoft : c.warnSoft} />
      </View>
    </View>
  );
}

const st = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: c.card, borderRadius: 12,
    borderWidth: 1, borderColor: c.border, padding: 12 },
  time: { width: 48 },
  title: { fontSize: 16, fontWeight: '700', color: c.text },
  sub: { fontSize: 12, color: c.muted },
  badges: { gap: 4, alignItems: 'flex-end' },
});
