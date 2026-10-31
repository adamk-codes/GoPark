import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { API_URL, api } from './src/api';
import type { Lot, LotOverview, ScanResult } from './src/contract';

// P0 skeleton: attendant mode can look up a lot and check a car in/out by booking ID or plate.
// P1 adds camera QR/plate scanning, the offline outbox, alerts, history and driver mode.
const DEVICE_ID = 'dev-attendant-1';
type Mode = 'attendant' | 'driver';

export default function App() {
  const [mode, setMode] = useState<Mode>('attendant');
  return (
    <View style={s.screen}>
      <View style={s.header}>
        <Text style={s.brand}>GoPark Sense</Text>
        <View style={s.row}>
          {(['attendant', 'driver'] as Mode[]).map((m) => (
            <Pressable key={m} onPress={() => setMode(m)} style={[s.tab, mode === m && s.tabActive]}>
              <Text style={[s.tabText, mode === m && s.tabTextActive]}>{m === 'attendant' ? 'Attendant' : 'Driver'}</Text>
            </Pressable>
          ))}
        </View>
      </View>
      {mode === 'attendant' ? <Attendant /> : <Driver />}
      <StatusBar style="dark" />
    </View>
  );
}

function Attendant() {
  const [lots, setLots] = useState<Lot[]>([]);
  const [lot, setLot] = useState('AUB');
  const [ov, setOv] = useState<LotOverview | null>(null);
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.overview(lot).then(setOv).then(() => setError(null))
      .catch((e) => setError(`Can't reach ${API_URL}. ${e}`));
  }, [lot]);

  useEffect(() => { api.lots().then(setLots).catch(() => undefined); }, []);
  useEffect(refresh, [refresh]);

  const submit = async () => {
    const q = query.trim();
    if (!q) return;
    const isBooking = /^B\d{3,}$/i.test(q);
    try {
      setResult(await api.scan({ lot_id: lot, device_id: DEVICE_ID, ...(isBooking ? { booking_id: q } : { plate: q }) }));
      setQuery('');
      refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <ScrollView contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.row}>
        {lots.map((l) => (
          <Pressable key={l.id} onPress={() => setLot(l.id)} style={[s.chip, lot === l.id && s.chipActive]}>
            <Text style={lot === l.id ? s.chipTextActive : s.chipText}>{l.id}</Text>
          </Pressable>
        ))}
      </ScrollView>

      {ov && (
        <View style={s.counts}>
          <Count label="Occupied" value={`${ov.counts.occupied}/${ov.counts.capacity}`} />
          <Count label="Reserved" value={String(ov.counts.reserved)} />
          <Count label="Free" value={String(ov.counts.free)} />
        </View>
      )}

      <Text style={s.label}>Booking ID or plate</Text>
      <TextInput value={query} onChangeText={setQuery} onSubmitEditing={submit} autoCapitalize="characters"
        placeholder="B017180 or G 270205" style={s.input} returnKeyType="go" />
      <Pressable onPress={submit} style={s.cta}><Text style={s.ctaText}>Check in / out</Text></Pressable>

      {result && (
        <View style={[s.result, result.result === 'checked_in' || result.result === 'checked_out' ? s.ok : s.warn]}>
          <Text style={s.resultTitle}>{result.result.replace('_', ' ')}</Text>
          <Text>{result.message}</Text>
        </View>
      )}
      {error && <Text style={s.error}>{error}</Text>}

      {ov && (
        <>
          <Text style={s.section}>Expected in the next 30 min</Text>
          {ov.expected_soon.slice(0, 12).map((e) => (
            <Pressable key={`${e.user_id}-${e.at}`} onPress={() => e.booking_id && setQuery(e.booking_id)} style={s.listRow}>
              <Text style={s.mono}>{e.at.slice(11, 16)}</Text>
              <Text style={s.flex}>{e.plate ?? e.user_id}</Text>
              <Text style={s.muted}>{e.source === 'prediction' ? 'habit' : e.booking_id}</Text>
            </Pressable>
          ))}
        </>
      )}
    </ScrollView>
  );
}

function Driver() {
  return (
    <View style={s.body}>
      <Text style={s.section}>Driver mode</Text>
      <Text style={s.muted}>Booking QR, one-tap confirmation and rewards arrive in phase P2–P3.</Text>
    </View>
  );
}

function Count({ label, value }: { label: string; value: string }) {
  return (
    <View style={s.count}>
      <Text style={s.countValue}>{value}</Text>
      <Text style={s.muted}>{label}</Text>
    </View>
  );
}

const green = '#178a54';
const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#f4f7f5', paddingTop: 48 },
  header: { paddingHorizontal: 16, gap: 8, paddingBottom: 8 },
  brand: { fontSize: 20, fontWeight: '700', color: green },
  row: { flexDirection: 'row', gap: 8 },
  tab: { paddingVertical: 6, paddingHorizontal: 14, borderRadius: 16, backgroundColor: '#e3ebe6' },
  tabActive: { backgroundColor: green },
  tabText: { color: '#14211b' },
  tabTextActive: { color: '#fff', fontWeight: '600' },
  body: { padding: 16, gap: 12 },
  chip: { paddingVertical: 6, paddingHorizontal: 12, borderRadius: 8, borderWidth: 1, borderColor: '#d9e3dd', backgroundColor: '#fff' },
  chipActive: { borderColor: green, backgroundColor: '#e3f3ea' },
  chipText: { color: '#14211b' },
  chipTextActive: { color: green, fontWeight: '700' },
  counts: { flexDirection: 'row', gap: 8 },
  count: { flex: 1, backgroundColor: '#fff', borderRadius: 10, padding: 12, borderWidth: 1, borderColor: '#d9e3dd' },
  countValue: { fontSize: 22, fontWeight: '700' },
  label: { fontWeight: '600' },
  input: { backgroundColor: '#fff', borderWidth: 1, borderColor: '#d9e3dd', borderRadius: 10, padding: 14, fontSize: 18 },
  cta: { backgroundColor: green, borderRadius: 12, paddingVertical: 16, alignItems: 'center' },
  ctaText: { color: '#fff', fontSize: 18, fontWeight: '700' },
  result: { borderRadius: 10, padding: 12, gap: 4 },
  ok: { backgroundColor: '#e3f3ea' },
  warn: { backgroundColor: '#fbeedf' },
  resultTitle: { fontWeight: '700', textTransform: 'capitalize' },
  error: { color: '#a2500f' },
  section: { fontSize: 16, fontWeight: '700', marginTop: 8 },
  listRow: { flexDirection: 'row', gap: 12, paddingVertical: 8, borderBottomWidth: 1, borderColor: '#e3ebe6' },
  mono: { fontVariant: ['tabular-nums'], fontWeight: '600' },
  flex: { flex: 1 },
  muted: { color: '#56655d' },
});
