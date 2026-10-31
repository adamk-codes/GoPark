import { useCallback } from 'react';
import { FlatList, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { Badge, ErrorLine, Muted, s } from '../../components/ui';
import type { AuditEntry, Method, Stay } from '../../contract';
import { hhmm } from '../../lib/format';
import { useApp } from '../../lib/settings';
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

export default function History() {
  const { api, settings } = useApp();
  const load = useCallback(() => api.audit(settings.lotId, 100), [api, settings.lotId]);
  const { data, error, refreshing, refresh } = useResource(load, `${settings.apiUrl}|${settings.lotId}`, POLL_MS);

  return (
    <View style={s.screen}>
      <FlatList
        data={data ?? []}
        keyExtractor={(e) => String(e.id)}
        contentContainerStyle={s.body}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={c.brand} />}
        ListHeaderComponent={<ErrorLine text={error && `${t.common.cantReach(settings.apiUrl)} · ${error}`} />}
        ListEmptyComponent={<Muted>{data ? t.history.empty : t.common.loading}</Muted>}
        renderItem={({ item }) => <Row e={item} />}
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
        {/* Everything read from the server is synced; the offline outbox (phase P2) adds pending rows. */}
        <Badge label={t.history.synced} fg={c.muted} bg={c.border} />
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
