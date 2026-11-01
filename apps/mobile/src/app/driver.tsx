import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { Badge, Button, Card, ErrorLine, Muted, Section, s } from '../components/ui';
import { peekBookingQr } from '../core/qr';
import type { PendingConfirmation } from '../contract';
import type { DriverBooking } from '../core/driver';
import { errorText, hhmm } from '../lib/format';
import { isSensing, startSensing, stopSensing, sensingSupport, type SensingSupport } from '../lib/geofence';
import { useApp } from '../lib/settings';
import { useDriver, type ConfirmDone } from '../lib/useDriver';
import { t } from '../strings';
import { c, toneColors, type Tone } from '../theme';

// Driver mode: one-tap confirm when the server isn't sure ("Did you just arrive?"), my bookings with their
// signed QR, and optional background arrival sensing.
export default function Driver() {
  const { api, settings, update: updateSettings } = useApp();
  const d = useDriver();
  const [userId, setUserId] = useState(settings.driverUserId);
  const [bookingId, setBookingId] = useState('');
  const [qrs, setQrs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [support, setSupport] = useState<SensingSupport | null>(null);
  const [sensing, setSensing] = useState(false);
  const [sensingNote, setSensingNote] = useState<string | null>(null);
  const lotName = (id: string) => d.cfg?.lots.find((l) => l.id === id)?.name ?? id;

  useEffect(() => {
    sensingSupport().then(setSupport);
    isSensing().then(setSensing);
  }, []);

  const pull = async () => {
    setRefreshing(true);
    await d.refresh();
    setRefreshing(false);
  };

  const saveUser = () => updateSettings({ driverUserId: userId.trim().toUpperCase() });

  // Adding a booking fetches its signed QR; lot and times come from the QR payload.
  const addBooking = async () => {
    if (!d.cfg) return;
    setBusy(true);
    setError(null);
    try {
      const id = bookingId.trim().toUpperCase();
      const { qr } = await api.bookingQr(id);
      const p = peekBookingQr(qr);
      if (!p?.bid || !p.lot || !p.s || !p.e) throw new Error(t.driver.badQr);
      const b: DriverBooking = { booking_id: p.bid, lot_id: p.lot, start: p.s, end: p.e };
      const next = await d.update({ bookings: [...d.cfg.bookings.filter((x) => x.booking_id !== b.booking_id), b] });
      setQrs((m) => ({ ...m, [b.booking_id]: qr }));
      setBookingId('');
      if (sensing) await startSensing(next, d.nowMs); // add the new lot to the geofences
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const removeBooking = async (id: string) => {
    if (!d.cfg) return;
    const next = await d.update({ bookings: d.cfg.bookings.filter((b) => b.booking_id !== id) });
    if (sensing) await startSensing(next, d.nowMs);
  };

  const toggleSensing = async (on: boolean) => {
    if (!d.cfg) return;
    setSensingNote(null);
    if (!on) {
      await stopSensing();
      await d.update({ sensing: false });
      setSensing(false);
      return;
    }
    const cfg = await d.update({ sensing: true });
    const r = await startSensing(cfg, d.nowMs);
    if (r !== 'started') await d.update({ sensing: false });
    setSensing(r === 'started');
    setSensingNote(t.driver.sensingResult[r]);
  };

  const { pending, failed, online } = d.outbox;
  const bookings = [...(d.cfg?.bookings ?? [])].sort((a, b) => a.start.localeCompare(b.start));

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.body} keyboardShouldPersistTaps="handled"
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={pull} tintColor={c.brand} />}>
      <Text style={s.label}>{t.driver.userId}</Text>
      <View style={s.row}>
        <TextInput value={userId} onChangeText={setUserId} autoCapitalize="characters" autoCorrect={false}
          style={[s.input, s.flex]} onSubmitEditing={saveUser} />
        <Button label={t.common.save} onPress={saveUser} disabled={!userId.trim() || userId.trim() === settings.driverUserId} />
      </View>
      <View style={s.row}>
        <Badge label={t.driver.outboxStatus(online, pending.length)}
          fg={online === false ? c.warn : c.ok} bg={online === false ? c.warnSoft : c.okSoft} />
        <View style={s.flex} />
        <Button kind="secondary" label={t.driver.rewardsLink} onPress={() => router.push('/rewards')} />
      </View>
      {d.error && !d.prompts.length && <Muted>{t.driver.offlinePrompts}</Muted>}

      {d.prompts.length > 0 && <Section title={t.driver.confirmTitle} />}
      {d.results.slice(0, 3).map((r) => <ResultCard key={r.id} r={r} onDismiss={() => d.dismiss(r.id)} />)}
      {d.prompts.map((p) => <ConfirmCard key={`${p.stay.id}|${p.question}`} p={p} lot={lotName(p.booking.location_id)}
        onAnswer={d.answer} />)}

      {pending.length > 0 && (
        <Card>
          <Text style={s.label}>{t.driver.waiting}</Text>
          {pending.map((r) => <Muted key={r.id}>{hhmm(r.queuedAt)} · {r.label}{r.lastError ? ` · ${r.lastError}` : ''}</Muted>)}
        </Card>
      )}
      {failed.map((f) => (
        <Card key={f.id} style={{ borderStartWidth: 5, borderStartColor: c.bad }}>
          <Text style={st.q}>{f.label}</Text>
          <Text style={{ color: c.bad, fontWeight: '700' }}>{t.driver.refused(f.status, f.reason)}</Text>
          <Button kind="secondary" label={t.sync.dismiss} onPress={() => d.dismiss(f.id)} />
        </Card>
      ))}

      <Section title={t.driver.myBookings} />
      <View style={s.row}>
        <TextInput value={bookingId} onChangeText={setBookingId} autoCapitalize="characters" autoCorrect={false}
          placeholder="B017180" placeholderTextColor={c.muted} style={[s.input, s.flex]} onSubmitEditing={addBooking} />
        <Button label={t.driver.addBooking} onPress={addBooking} busy={busy} disabled={!bookingId.trim()} />
      </View>
      <ErrorLine text={error} />
      {bookings.length === 0 && <Muted>{t.driver.noBookings}</Muted>}
      {bookings.map((b) => (
        <Card key={b.booking_id}>
          <View style={s.row}>
            <View style={s.flex}>
              <Text style={st.q}>{lotName(b.lot_id)}</Text>
              <Muted>{b.booking_id} · {b.start.slice(5, 10)} {hhmm(b.start)}–{hhmm(b.end)}</Muted>
            </View>
            <Button kind="secondary" label={t.driver.remove} onPress={() => removeBooking(b.booking_id)} />
          </View>
          {qrs[b.booking_id] && (
            <>
              <Text style={s.label}>{t.driver.qrString}</Text>
              <Text selectable style={st.qr}>{qrs[b.booking_id]}</Text>
            </>
          )}
        </Card>
      ))}

      <Section title={t.driver.sensingTitle} />
      <Card>
        <View style={s.row}>
          <Text style={[s.label, s.flex]}>{t.driver.sensingSwitch}</Text>
          <Switch value={sensing} onValueChange={toggleSensing} disabled={support !== 'ok'} trackColor={{ true: c.brand }} />
        </View>
        <Muted>{support === 'ok' ? t.driver.sensingHelp : t.driver.sensingResult[support ?? 'needs_dev_build']}</Muted>
        {sensingNote && <Text style={{ color: sensing ? c.ok : c.warn, fontWeight: '700' }}>{sensingNote}</Text>}
      </Card>
    </ScrollView>
  );
}

function ConfirmCard({ p, lot, onAnswer }: { p: PendingConfirmation; lot: string;
  onAnswer: ReturnType<typeof useDriver>['answer'] }) {
  const arrival = p.question === 'arrived';
  const b = p.booking;
  const sub = [b.plate, `${hhmm(b.start_time)}–${hhmm(b.end_time)}`, arrival && p.p != null && t.driver.seenWith(p.p)];
  return (
    <Card style={{ borderStartWidth: 5, borderStartColor: c.brand }}>
      <Text style={st.q}>{arrival ? t.driver.didArrive(lot) : t.driver.didLeave(lot)}</Text>
      <Muted>{sub.filter(Boolean).join(' · ')}</Muted>
      <View style={s.row}>
        {arrival ? (
          <>
            <View style={s.flex}><Button big label={t.driver.arrived} onPress={() => onAnswer(p, 'arrived', t.driver.labelArrived(lot))} /></View>
            <View style={s.flex}><Button big kind="secondary" label={t.driver.notMe} onPress={() => onAnswer(p, 'not_me', t.driver.labelNotMe(lot))} /></View>
          </>
        ) : (
          <>
            <View style={s.flex}><Button big label={t.driver.left} onPress={() => onAnswer(p, 'left', t.driver.labelLeft(lot))} /></View>
            <View style={s.flex}><Button big kind="secondary" label={t.driver.stillHere} onPress={() => onAnswer(p, null, '')} /></View>
          </>
        )}
      </View>
    </Card>
  );
}

const resultTone: Record<ConfirmDone['response']['result'], Tone> = {
  checked_in: 'ok', checked_out: 'ok', rejected: 'info', disputed: 'warn', already: 'info',
};

// What the server made of an answer, with the points it earned.
function ResultCard({ r, onDismiss }: { r: ConfirmDone; onDismiss: () => void }) {
  const tone = toneColors[resultTone[r.response.result] ?? 'info'];
  const pts = r.response.points_awarded;
  return (
    <Card style={{ borderStartWidth: 5, borderStartColor: tone.fg, backgroundColor: tone.bg }}>
      <View style={s.row}>
        <Text style={[st.q, s.flex]}>{r.label}</Text>
        {pts > 0 && <Badge label={t.driver.pointsAwarded(pts)} fg={c.ok} bg={c.okSoft} />}
      </View>
      <Text style={{ color: c.text, fontSize: 16 }}>{r.response.message}</Text>
      {pts === 0 && (r.response.result === 'checked_in' || r.response.result === 'checked_out')
        && <Muted>{t.driver.noPoints}</Muted>}
      <Button kind="secondary" label={t.driver.ok} onPress={onDismiss} />
    </Card>
  );
}

const st = StyleSheet.create({
  q: { fontSize: 18, fontWeight: '800', color: c.text },
  qr: { fontFamily: 'monospace', color: c.text, fontSize: 12 },
});
