// Shared building blocks. Layout uses start/end (never left/right) so the app flips cleanly for Arabic (RTL).
import type { ReactNode } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { c } from '../theme';

export function Card({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return <View style={[s.card, style]}>{children}</View>;
}

export function Section({ title, right }: { title: string; right?: ReactNode }) {
  return (
    <View style={s.sectionRow}>
      <Text style={s.section}>{title}</Text>
      {right}
    </View>
  );
}

export function Badge({ label, fg, bg }: { label: string; fg: string; bg: string }) {
  return (
    <View style={[s.badge, { backgroundColor: bg }]}>
      <Text style={[s.badgeText, { color: fg }]}>{label}</Text>
    </View>
  );
}

export function Button({ label, onPress, kind = 'primary', busy, disabled, big }: {
  label: string; onPress: () => void; kind?: 'primary' | 'secondary' | 'danger'; busy?: boolean; disabled?: boolean;
  big?: boolean;
}) {
  const off = disabled || busy;
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      disabled={off}
      style={({ pressed }) => [s.btn, big && s.btnBig, s[kind], (pressed || off) && s.pressed]}
    >
      {busy ? <ActivityIndicator color={kind === 'secondary' ? c.brand : '#fff'} /> : (
        <Text style={[s.btnText, big && s.btnTextBig, kind === 'secondary' && s.btnTextSecondary]}>{label}</Text>
      )}
    </Pressable>
  );
}

export function ErrorLine({ text }: { text: string | null }) {
  return text ? <Text style={s.error}>{text}</Text> : null;
}

export function Muted({ children }: { children: ReactNode }) {
  return <Text style={s.muted}>{children}</Text>;
}

export const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: c.bg },
  body: { padding: 16, gap: 12, paddingBottom: 32 },
  card: { backgroundColor: c.card, borderRadius: 12, borderWidth: 1, borderColor: c.border, padding: 14, gap: 6 },
  sectionRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 },
  section: { fontSize: 17, fontWeight: '700', color: c.text },
  badge: { borderRadius: 999, paddingHorizontal: 8, paddingVertical: 2, alignSelf: 'flex-start' },
  badgeText: { fontSize: 12, fontWeight: '700' },
  btn: { borderRadius: 12, paddingVertical: 14, paddingHorizontal: 16, alignItems: 'center', justifyContent: 'center',
    minHeight: 52 },
  btnBig: { paddingVertical: 18, minHeight: 64 },
  primary: { backgroundColor: c.brand },
  secondary: { backgroundColor: c.card, borderWidth: 2, borderColor: c.brand },
  danger: { backgroundColor: c.bad },
  pressed: { opacity: 0.6 },
  btnText: { color: '#fff', fontSize: 17, fontWeight: '700' },
  btnTextBig: { fontSize: 20 },
  btnTextSecondary: { color: c.brand },
  error: { color: c.bad, fontWeight: '600' },
  muted: { color: c.muted },
  label: { fontWeight: '700', color: c.text },
  input: { backgroundColor: c.card, borderWidth: 1, borderColor: c.border, borderRadius: 10, padding: 14, fontSize: 18,
    color: c.text, textAlign: 'auto' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  flex: { flex: 1 },
  mono: { fontVariant: ['tabular-nums'], fontWeight: '700', color: c.text },
  chip: { paddingVertical: 10, paddingHorizontal: 14, borderRadius: 10, borderWidth: 1, borderColor: c.border,
    backgroundColor: c.card },
  chipActive: { borderColor: c.brand, backgroundColor: c.brandSoft },
  chipText: { color: c.text, fontWeight: '600' },
  chipTextActive: { color: c.brand, fontWeight: '800' },
});
