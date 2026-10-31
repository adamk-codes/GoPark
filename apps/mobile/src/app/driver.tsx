import { useState } from 'react';
import { ScrollView, Text, TextInput, View } from 'react-native';
import { Button, Card, ErrorLine, Muted, s } from '../components/ui';
import { errorText, hhmm } from '../lib/format';
import { useApp } from '../lib/settings';
import type { BookingQr, Rewards } from '../pending-contract';
import { t } from '../strings';
import { c } from '../theme';

// Phase P1 preview of driver mode. P3 adds the rendered QR code, "On my way" and one-tap confirm.
export default function Driver() {
  const { api, settings, update } = useApp();
  const [userId, setUserId] = useState(settings.driverUserId);
  const [bookingId, setBookingId] = useState('');
  const [rewards, setRewards] = useState<Rewards | null>(null);
  const [qr, setQr] = useState<{ data: BookingQr; mocked: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'rewards' | 'qr' | null>(null);

  const loadRewards = async () => {
    setBusy('rewards');
    setError(null);
    try {
      await update({ driverUserId: userId.trim() });
      setRewards(await api.rewards(userId.trim()));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const loadQr = async () => {
    setBusy('qr');
    setError(null);
    try {
      setQr(await api.bookingQr(bookingId.trim().toUpperCase(), settings.lotId));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">
      <Muted>{t.driver.intro}</Muted>

      <Text style={s.label}>{t.driver.userId}</Text>
      <View style={s.row}>
        <TextInput value={userId} onChangeText={setUserId} autoCapitalize="characters" autoCorrect={false}
          style={[s.input, s.flex]} />
        <Button label={t.driver.load} onPress={loadRewards} busy={busy === 'rewards'} disabled={!userId.trim()} />
      </View>
      {rewards && (
        <Card>
          <Text style={s.label}>{t.driver.rewards}</Text>
          <View style={s.row}>
            <Stat value={String(rewards.points)} label={t.driver.points} />
            <Stat value={String(rewards.grace_minutes)} label={t.driver.grace} />
            <Stat value={rewards.tier} label={t.driver.tier} />
          </View>
        </Card>
      )}

      <Text style={s.label}>{t.driver.bookingId}</Text>
      <View style={s.row}>
        <TextInput value={bookingId} onChangeText={setBookingId} autoCapitalize="characters" autoCorrect={false}
          placeholder="B017180" placeholderTextColor={c.muted} style={[s.input, s.flex]} />
        <Button label={t.driver.showQr} onPress={loadQr} busy={busy === 'qr'} disabled={!bookingId.trim()} />
      </View>
      {qr && (
        <Card>
          <Text style={s.label}>{t.driver.qrString}{qr.mocked ? ` (${t.common.mock})` : ''}</Text>
          <Text selectable style={{ fontFamily: 'monospace', color: c.text }}>{qr.data.qr}</Text>
          {qr.data.valid_from ? <Muted>{t.driver.validFrom(hhmm(qr.data.valid_from), hhmm(qr.data.valid_until))}</Muted> : null}
        </Card>
      )}
      <ErrorLine text={error} />
    </ScrollView>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <View style={s.flex}>
      <Text style={{ fontSize: 24, fontWeight: '800', color: c.brand }}>{value}</Text>
      <Muted>{label}</Muted>
    </View>
  );
}
