import { useEffect, useMemo, useState } from 'react';
import { ScrollView, Pressable, Switch, Text, TextInput, View } from 'react-native';
import { createApi } from '../api';
import { Button, Card, ErrorLine, Muted, s } from '../components/ui';
import type { Lot } from '../contract';
import { errorText, hhmm } from '../lib/format';
import { newDeviceId } from '../lib/ids';
import { useApp } from '../lib/settings';
import { t } from '../strings';
import { c } from '../theme';

export default function SettingsScreen() {
  const { settings, update } = useApp();
  const [apiUrl, setApiUrl] = useState(settings.apiUrl);
  const [deviceId, setDeviceId] = useState(settings.deviceId);
  const [lots, setLots] = useState<Lot[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);

  // Test against the URL being typed, before it is saved.
  const draftApi = useMemo(
    () => createApi({ baseUrl: apiUrl.trim(), deviceId, devMocks: settings.devMocks, timeoutMs: 5000 }),
    [apiUrl, deviceId, settings.devMocks],
  );

  const test = async () => {
    setTesting(true);
    setError(null);
    setStatus(null);
    try {
      const [h, ls] = await Promise.all([draftApi.health(), draftApi.lots()]);
      setStatus(t.settings.connected(`${h.now.slice(0, 10)} ${hhmm(h.now)}`, h.version));
      setLots(ls);
    } catch (e) {
      setError(`${t.common.cantReach(apiUrl)} · ${errorText(e)}`);
    } finally {
      setTesting(false);
    }
  };

  // Load lots once on open with the saved URL.
  useEffect(() => {
    test();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    await update({ apiUrl: apiUrl.trim(), deviceId: deviceId.trim() || settings.deviceId });
    setStatus(t.common.saved);
  };

  const dirty = apiUrl.trim() !== settings.apiUrl || deviceId.trim() !== settings.deviceId;

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">
      <Text style={s.label}>{t.settings.apiUrl}</Text>
      <TextInput value={apiUrl} onChangeText={setApiUrl} autoCapitalize="none" autoCorrect={false} keyboardType="url"
        style={s.input} placeholder="http://192.168.1.20:8000" placeholderTextColor={c.muted} />
      <Muted>{t.settings.apiUrlHint}</Muted>
      <Button kind="secondary" label={t.settings.test} onPress={test} busy={testing} />
      {status && <Text style={{ color: c.ok, fontWeight: '700' }}>{status}</Text>}
      <ErrorLine text={error} />

      <Text style={s.label}>{t.settings.lot}</Text>
      {lots.length === 0 ? <Muted>{t.settings.noLots}</Muted> : (
        <View style={[s.row, { flexWrap: 'wrap' }]}>
          {lots.map((l) => {
            const on = l.id === settings.lotId;
            return (
              <Pressable key={l.id} onPress={() => update({ lotId: l.id })} style={[s.chip, on && s.chipActive]}
                accessibilityRole="button" accessibilityState={{ selected: on }}>
                <Text style={on ? s.chipTextActive : s.chipText}>{l.id}</Text>
                <Text style={{ fontSize: 11, color: c.muted }}>{l.name}</Text>
              </Pressable>
            );
          })}
        </View>
      )}

      <Text style={s.label}>{t.settings.deviceId}</Text>
      <View style={s.row}>
        <TextInput value={deviceId} onChangeText={setDeviceId} autoCapitalize="none" autoCorrect={false}
          style={[s.input, s.flex]} />
        <Button kind="secondary" label={t.settings.regenerate} onPress={() => setDeviceId(newDeviceId())} />
      </View>

      <Button big label={t.common.save} onPress={save} disabled={!dirty} />

      <Card>
        <View style={s.row}>
          <Text style={[s.label, s.flex]}>{t.settings.mocks}</Text>
          <Switch value={settings.devMocks} onValueChange={(v) => update({ devMocks: v })}
            trackColor={{ true: c.brand }} />
        </View>
        <Muted>{settings.devMocks ? t.settings.mocksOn : t.settings.mocksOff}</Muted>
      </Card>
    </ScrollView>
  );
}
