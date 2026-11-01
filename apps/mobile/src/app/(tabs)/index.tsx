import { router } from 'expo-router';
import { useCallback } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { Badge, Card, ErrorLine, Muted, Section, s } from '../../components/ui';
import type { ExpectedArrival, LotOverview } from '../../contract';
import { hhmm, minutesBetween } from '../../lib/format';
import { useApp } from '../../lib/settings';
import { useSync } from '../../lib/sync';
import { useResource } from '../../lib/useResource';
import { t } from '../../strings';
import { c } from '../../theme';

const POLL_MS = 10_000;

export default function Overview() {
  const { api, settings } = useApp();
  const engine = useSync();
  const load = useCallback(() => api.overview(settings.lotId), [api, settings.lotId]);
  const { data: ov, error, savedAt, refreshing, refresh } = useResource(load, `${settings.apiUrl}|${settings.lotId}`,
    POLL_MS, `overview:${settings.lotId}`);
  // Check-ins and walk-ins done on this phone that the server (and so these counts) hasn't seen yet.
  const unsynced = engine.pendingItems().filter((p) => p.local?.result === 'checked_in').length;

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.body}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={c.brand} />}>
      {error && !ov && <ErrorLine text={`${t.common.cantReach(settings.apiUrl)} · ${error}`} />}
      {error && ov && savedAt !== null && <Muted>{t.sync.lastUpdated(hhmm(ov.now))}</Muted>}
      {!ov && !error && <Muted>{t.common.loading}</Muted>}
      {unsynced > 0 && <Badge label={t.overview.unsynced(unsynced)} fg={c.warn} bg={c.warnSoft} />}
      {ov && <Body ov={ov} />}
    </ScrollView>
  );
}

function Body({ ov }: { ov: LotOverview }) {
  const { counts } = ov;
  const pct = (n: number) => `${counts.capacity ? Math.min(100, (n / counts.capacity) * 100) : 0}%` as const;
  return (
    <>
      <View style={s.row}>
        <Text style={st.lotName}>{ov.lot.name}</Text>
        <Badge label={t.overview.demoTime(hhmm(ov.now))} fg={c.muted} bg={c.border} />
      </View>

      <View style={s.row}>
        <Count label={t.overview.occupied} value={counts.occupied} sub={t.overview.capacity(counts.capacity)} color={c.bad} />
        <Count label={t.overview.reserved} value={counts.reserved} color={c.warn} />
        <Count label={t.overview.free} value={counts.free} color={c.ok} />
      </View>
      <View style={st.bar} accessibilityLabel={`${counts.occupied} ${t.overview.occupied}, ${counts.reserved} ${t.overview.reserved}`}>
        <View style={[st.barPart, { width: pct(counts.occupied), backgroundColor: c.bad }]} />
        <View style={[st.barPart, { width: pct(counts.reserved), backgroundColor: c.warn }]} />
      </View>

      {ov.spaces && (
        <>
          <Section title={t.overview.spots} />
          <View style={st.grid}>
            {ov.spaces.map((sp) => (
              <View key={sp.id} style={[st.spot, { backgroundColor: c[sp.state] }]} accessibilityLabel={`${sp.label} ${sp.state}`}>
                <Text style={st.spotText}>{sp.label}</Text>
              </View>
            ))}
          </View>
        </>
      )}

      <Section title={t.overview.expectedSoon} />
      {ov.expected_soon.length === 0 ? <Muted>{t.overview.nobodyExpected}</Muted> : (
        <Card style={{ padding: 0, gap: 0 }}>
          {ov.expected_soon.map((e) => <Expected key={`${e.user_id}-${e.at}`} e={e} now={ov.now} />)}
        </Card>
      )}
      {ov.expected_soon.some((e) => e.booking_id) && <Muted>{t.overview.tapToScan}</Muted>}
    </>
  );
}

function Count({ label, value, sub, color }: { label: string; value: number; sub?: string; color: string }) {
  return (
    <Card style={s.flex}>
      <Text style={[st.countValue, { color }]}>{value}</Text>
      <Text style={s.muted}>{label}{sub ? ` ${sub}` : ''}</Text>
    </Card>
  );
}

function Expected({ e, now }: { e: ExpectedArrival; now: string }) {
  const q = e.booking_id ?? e.plate;
  const open = () => q && router.navigate({ pathname: '/scan', params: { q } });
  return (
    <Pressable onPress={open} disabled={!q} style={({ pressed }) => [st.expRow, pressed && s.pressed]}
      accessibilityRole={q ? 'button' : undefined}>
      <View style={st.expTime}>
        <Text style={s.mono}>{hhmm(e.at)}</Text>
        <Text style={st.small}>{t.overview.inMin(minutesBetween(now, e.at))}</Text>
      </View>
      <View style={s.flex}>
        <Text style={st.plate}>{e.plate ?? e.user_id}</Text>
        <Text style={st.small}>{e.booking_id ?? t.overview.habit}</Text>
      </View>
      <Badge label={t.overview.probability(e.p)}
        fg={e.source === 'prediction' ? c.info : c.ok} bg={e.source === 'prediction' ? c.infoSoft : c.okSoft} />
      {q && <Text style={st.chev}>›</Text>}
    </Pressable>
  );
}

const st = StyleSheet.create({
  lotName: { flex: 1, fontSize: 20, fontWeight: '800', color: c.text },
  countValue: { fontSize: 30, fontWeight: '800' },
  bar: { height: 10, borderRadius: 5, backgroundColor: c.okSoft, flexDirection: 'row', overflow: 'hidden' },
  barPart: { height: '100%' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  spot: { width: 56, height: 40, borderRadius: 8, alignItems: 'center', justifyContent: 'center', borderWidth: 1,
    borderColor: c.border },
  spotText: { fontSize: 12, fontWeight: '700', color: c.text },
  expRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, paddingHorizontal: 14,
    borderBottomWidth: StyleSheet.hairlineWidth, borderColor: c.border, minHeight: 56 },
  expTime: { width: 64 },
  plate: { fontSize: 16, fontWeight: '700', color: c.text },
  small: { fontSize: 12, color: c.muted },
  chev: { fontSize: 24, color: c.muted },
});
