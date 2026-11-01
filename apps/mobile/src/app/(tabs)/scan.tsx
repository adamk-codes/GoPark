import { CameraView, useCameraPermissions, type BarcodeScanningResult } from 'expo-camera';
import { useIsFocused, useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { classifyInput, type ScanInput } from '../../api';
import { Badge, Button, Card, ErrorLine, s } from '../../components/ui';
import type { Action } from '../../core/sync';
import { useScanFeedback } from '../../lib/feedback';
import { hhmm } from '../../lib/format';
import { useSync } from '../../lib/sync';
import { t } from '../../strings';
import { c, resultTone, toneColors } from '../../theme';

const SAME_CODE_COOLDOWN_MS = 4000; // the camera reports the same QR many times a second

interface Outcome {
  key: number; // remounts the card (and its walk-in field) for every new result
  action: Action;
  input: ScanInput;
}

export default function Scan() {
  const engine = useSync();
  const { q } = useLocalSearchParams<{ q?: string }>();
  const focused = useIsFocused();
  const [permission, requestPermission] = useCameraPermissions();
  const [cameraOn, setCameraOn] = useState(true);
  const [query, setQuery] = useState('');
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [error, setError] = useState<string | null>(null);
  const feedback = useScanFeedback();
  const lastCode = useRef<{ data: string; at: number } | null>(null);

  // Tapping an "expected soon" row on Overview lands here with the booking prefilled.
  useEffect(() => {
    if (q) setQuery(String(q));
  }, [q]);

  // Decided on the phone from the cache at once, then queued for the server: no waiting on the network.
  const send = useCallback((input: ScanInput) => {
    setError(null);
    const action = engine.scan(input);
    setOutcome({ key: Date.now(), action, input });
    feedback(action.decision.unverified ? 'warn' : resultTone[action.decision.result!.result]);
    setQuery('');
  }, [engine, feedback]);

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

  const walkIn = useCallback((plate: string) => {
    setError(null);
    const action = engine.walkIn(plate);
    if (typeof action === 'string') {
      setError(action);
      feedback('bad');
      return;
    }
    setOutcome({ key: Date.now(), action, input: { plate } });
    feedback(resultTone[action.decision.result!.result]);
  }, [engine, feedback]);

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
              onBarcodeScanned={onBarcode}
            />
            <View style={st.reticle} pointerEvents="none" />
            <Text style={st.cameraHint}>{t.scan.cameraTitle}</Text>
          </View>
        ) : null}
        <Button kind="secondary" label={cameraOn ? t.scan.hideCamera : t.scan.cameraOn} onPress={() => setCameraOn((v) => !v)} />

        {outcome && <ResultCard key={outcome.key} outcome={outcome} onWalkIn={walkIn} />}
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
        <Button big label={t.scan.submit} onPress={submitManual} disabled={!query.trim()} />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function ResultCard({ outcome, onWalkIn }: { outcome: Outcome; onWalkIn: (plate: string) => void }) {
  const engine = useSync(); // re-renders when the queued event syncs
  const { action, input } = outcome;
  const sync = engine.outcome(action.id);
  // The server's answer wins once it is back; until then, the phone's.
  const res = sync?.server ?? action.decision.result!;
  const unverified = !sync?.server && action.decision.unverified;
  const tone = toneColors[unverified ? 'warn' : resultTone[res.result]];
  const typedPlate = 'plate' in input ? input.plate : '';
  const [plate, setPlate] = useState(typedPlate || res.booking?.plate || '');
  const b = res.booking;
  const showWalkIn = res.suggest === 'register_walk_in';
  const badge = !sync ? { label: t.sync.offlineCheck, fg: c.muted, bg: c.border }
    : sync.status === 'synced' ? { label: t.sync.synced, fg: c.ok, bg: c.okSoft }
      : sync.status === 'conflict' ? { label: t.sync.conflict, fg: c.bad, bg: c.badSoft }
        : { label: t.sync.pending, fg: c.warn, bg: c.warnSoft };

  return (
    <View style={[st.result, { backgroundColor: tone.bg, borderColor: tone.fg }]} accessibilityLiveRegion="polite">
      <View style={s.row}>
        <Text style={[st.resultTitle, { color: tone.fg }, s.flex]}>{unverified ? t.sync.unverified : t.result[res.result]}</Text>
        <Badge {...badge} />
      </View>
      <Text style={st.resultMsg}>{res.message}</Text>
      {sync?.status === 'conflict' && <Text style={st.conflict}>{sync.reason}</Text>}
      {b && (
        <View style={st.details}>
          <Detail k={t.scan.plate} v={b.plate ?? '–'} />
          <Detail k={t.scan.booking} v={b.booking_id} />
          <Detail k={t.scan.lotLabel} v={b.location_id} />
          <Detail k={t.scan.window} v={`${hhmm(b.start_time)}–${hhmm(b.end_time)}`} />
        </View>
      )}
      {!b && res.stay?.plate_norm && <Detail k={t.scan.plate} v={res.stay.plate_norm} />}
      {showWalkIn && (
        <View style={st.walkIn}>
          {!typedPlate && (
            <TextInput value={plate} onChangeText={setPlate} placeholder={t.scan.walkInPlate} autoCapitalize="characters"
              autoCorrect={false} placeholderTextColor={c.muted} style={s.input} />
          )}
          <Button big label={t.scan.registerWalkIn} disabled={!plate.trim()} onPress={() => onWalkIn(plate.trim())} />
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
  conflict: { fontSize: 15, color: c.bad, fontWeight: '700' },
});
