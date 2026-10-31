import { CameraView, useCameraPermissions, type BarcodeScanningResult } from 'expo-camera';
import { useIsFocused, useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { classifyInput, type ScanInput } from '../../api';
import { Button, Card, ErrorLine, Muted, s } from '../../components/ui';
import type { ScanResult } from '../../contract';
import { useScanFeedback } from '../../lib/feedback';
import { errorText, hhmm } from '../../lib/format';
import { useApp } from '../../lib/settings';
import { t } from '../../strings';
import { c, resultTone, toneColors } from '../../theme';

const SAME_CODE_COOLDOWN_MS = 4000; // the camera reports the same QR many times a second

interface Outcome {
  id: number; // remounts the card (and its walk-in field) for every new result
  res: ScanResult;
  input: ScanInput;
  mocked?: boolean;
}

export default function Scan() {
  const { api, settings } = useApp();
  const { q } = useLocalSearchParams<{ q?: string }>();
  const focused = useIsFocused();
  const [permission, requestPermission] = useCameraPermissions();
  const [cameraOn, setCameraOn] = useState(true);
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [error, setError] = useState<string | null>(null);
  const feedback = useScanFeedback();
  const inFlight = useRef(false);
  const lastCode = useRef<{ data: string; at: number } | null>(null);

  // Tapping an "expected soon" row on Overview lands here with the booking prefilled.
  useEffect(() => {
    if (q) setQuery(String(q));
  }, [q]);

  const send = useCallback(async (input: ScanInput) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const res = await api.scan(settings.lotId, input);
      setOutcome({ id: Date.now(), res, input });
      feedback(resultTone[res.result]);
      setQuery('');
    } catch (e) {
      setError(`${t.scan.networkError} (${errorText(e)})`);
      feedback('bad');
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, [api, settings.lotId, feedback]);

  const onBarcode = useCallback((r: BarcodeScanningResult) => {
    const now = Date.now();
    if (lastCode.current && lastCode.current.data === r.data && now - lastCode.current.at < SAME_CODE_COOLDOWN_MS) return;
    lastCode.current = { data: r.data, at: now };
    const input = classifyInput(r.data);
    if (input) send(input);
  }, [send]);

  const submitManual = () => {
    const input = classifyInput(query);
    if (input) send(input);
  };

  const walkIn = useCallback(async (plate: string) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const { data, mocked } = await api.walkIn(settings.lotId, plate);
      setOutcome({ id: Date.now(), res: data, input: { plate }, mocked });
      feedback(resultTone[data.result]);
    } catch (e) {
      setError(`${t.scan.networkError} (${errorText(e)})`);
      feedback('bad');
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, [api, settings.lotId, feedback]);

  // Only one camera preview may run at a time: unmount it whenever this tab is not on screen.
  const showCamera = focused && cameraOn && permission?.granted;

  return (
    <KeyboardAvoidingView style={s.screen} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView contentContainerStyle={s.body} keyboardShouldPersistTaps="handled">
        {cameraOn && permission && !permission.granted ? (
          <Card style={st.permission}>
            <Text style={s.label}>{t.scan.needPermission}</Text>
            <Button label={t.scan.grant} onPress={requestPermission} />
          </Card>
        ) : null}
        {showCamera ? (
          <View style={st.cameraBox}>
            <CameraView
              style={StyleSheet.absoluteFill}
              facing="back"
              barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
              onBarcodeScanned={busy ? undefined : onBarcode}
            />
            <View style={st.reticle} pointerEvents="none" />
            <Text style={st.cameraHint}>{t.scan.cameraTitle}</Text>
          </View>
        ) : null}
        <Button kind="secondary" label={cameraOn ? t.scan.hideCamera : t.scan.cameraOn} onPress={() => setCameraOn((v) => !v)} />

        {outcome && <ResultCard key={outcome.id} outcome={outcome} busy={busy} onWalkIn={walkIn} />}
        <ErrorLine text={error} />

        <Text style={s.label}>{t.scan.manualLabel}</Text>
        <TextInput
          value={query}
          onChangeText={setQuery}
          onSubmitEditing={submitManual}
          autoCapitalize="characters"
          autoCorrect={false}
          placeholder={t.scan.manualPlaceholder}
          placeholderTextColor={c.muted}
          returnKeyType="go"
          style={s.input}
        />
        <Button big label={busy ? t.scan.sending : t.scan.submit} onPress={submitManual} busy={busy} disabled={!query.trim()} />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function ResultCard({ outcome, busy, onWalkIn }: { outcome: Outcome; busy: boolean; onWalkIn: (plate: string) => void }) {
  const { res, input, mocked } = outcome;
  const tone = toneColors[resultTone[res.result]];
  const typedPlate = 'plate' in input ? input.plate : '';
  const [plate, setPlate] = useState(typedPlate || res.booking?.plate || '');
  const b = res.booking;
  const showWalkIn = res.suggest === 'register_walk_in';

  return (
    <View style={[st.result, { backgroundColor: tone.bg, borderColor: tone.fg }]} accessibilityLiveRegion="polite">
      <Text style={[st.resultTitle, { color: tone.fg }]}>{t.result[res.result]}</Text>
      <Text style={st.resultMsg}>{res.message}</Text>
      {b && (
        <View style={st.details}>
          <Detail k={t.scan.plate} v={b.plate ?? '–'} />
          <Detail k={t.scan.booking} v={b.booking_id} />
          <Detail k={t.scan.lotLabel} v={b.location_id} />
          <Detail k={t.scan.window} v={`${hhmm(b.start_time)}–${hhmm(b.end_time)}`} />
        </View>
      )}
      {!b && res.stay?.plate_norm && <Detail k={t.scan.plate} v={res.stay.plate_norm} />}
      {mocked && <Muted>{t.scan.walkInMocked}</Muted>}
      {showWalkIn && (
        <View style={st.walkIn}>
          {!typedPlate && (
            <TextInput value={plate} onChangeText={setPlate} placeholder={t.scan.walkInPlate} autoCapitalize="characters"
              autoCorrect={false} placeholderTextColor={c.muted} style={s.input} />
          )}
          <Button big label={t.scan.registerWalkIn} busy={busy} disabled={!plate.trim()} onPress={() => onWalkIn(plate.trim())} />
        </View>
      )}
    </View>
  );
}

function Detail({ k, v }: { k: string; v: string }) {
  return (
    <View style={st.detail}>
      <Text style={st.detailKey}>{k}</Text>
      <Text style={st.detailVal}>{v}</Text>
    </View>
  );
}

const st = StyleSheet.create({
  permission: { gap: 10 },
  cameraBox: { height: 280, borderRadius: 16, overflow: 'hidden', backgroundColor: '#000', alignItems: 'center',
    justifyContent: 'center' },
  reticle: { width: 190, height: 190, borderWidth: 3, borderColor: '#ffffffcc', borderRadius: 18 },
  cameraHint: { position: 'absolute', bottom: 10, color: '#fff', fontWeight: '700', textShadowColor: '#000',
    textShadowRadius: 4 },
  result: { borderRadius: 16, borderWidth: 3, padding: 18, gap: 8 },
  resultTitle: { fontSize: 32, fontWeight: '900' },
  resultMsg: { fontSize: 18, color: c.text, fontWeight: '600' },
  details: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginTop: 4 },
  detail: { minWidth: 120 },
  detailKey: { fontSize: 12, color: c.muted, fontWeight: '700' },
  detailVal: { fontSize: 17, color: c.text, fontWeight: '800', fontVariant: ['tabular-nums'] },
  walkIn: { gap: 8, marginTop: 6 },
});
