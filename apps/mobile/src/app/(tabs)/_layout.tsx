import { router } from 'expo-router';
import { Tabs } from 'expo-router/js-tabs';
import { Pressable, StyleSheet, Text, View, type ColorValue } from 'react-native';
import { useApp } from '../../lib/settings';
import { useSync } from '../../lib/sync';
import { t } from '../../strings';
import { c } from '../../theme';

// Glyph icons: no icon font to load, and they work in plain Expo Go.
function Glyph({ ch, color }: { ch: string; color: ColorValue }) {
  return <Text style={{ fontSize: 22, color }}>{ch}</Text>;
}

// "Online" / "Offline · 3 pending" under the title. Tapping it syncs now.
function HeaderTitle({ lotId }: { lotId: string }) {
  const engine = useSync();
  const { online, pending } = engine.status();
  const color = online === false ? c.warn : online ? c.ok : c.muted;
  return (
    <Pressable onPress={() => engine.kick()} accessibilityRole="button" accessibilityLabel={t.sync.syncNow}
      style={st.title} hitSlop={6}>
      <Text style={st.titleText} numberOfLines={1}>{t.appName} · {lotId}</Text>
      <View style={st.statusRow}>
        <View style={[st.dot, { backgroundColor: color }]} />
        <Text style={[st.statusText, { color }]} numberOfLines={1}>{t.sync.status(online, pending)}</Text>
      </View>
    </Pressable>
  );
}

function HeaderActions() {
  return (
    <View style={st.actions}>
      <Pressable accessibilityRole="button" hitSlop={8} onPress={() => router.push('/driver')} style={st.action}>
        <Text style={st.actionText}>{t.header.driver}</Text>
      </Pressable>
      <Pressable accessibilityRole="button" accessibilityLabel={t.header.settings} hitSlop={8}
        onPress={() => router.push('/settings')} style={st.action}>
        <Text style={st.gear}>⚙</Text>
      </Pressable>
    </View>
  );
}

export default function TabLayout() {
  const { settings } = useApp();
  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: c.brand,
        tabBarLabelStyle: { fontSize: 13, fontWeight: '700' },
        headerTitle: () => <HeaderTitle lotId={settings.lotId} />,
        headerTintColor: c.text,
        headerRight: () => <HeaderActions />,
      }}
    >
      <Tabs.Screen name="index" options={{ title: t.tabs.overview, tabBarIcon: ({ color }) => <Glyph ch="▦" color={color} /> }} />
      <Tabs.Screen name="scan" options={{ title: t.tabs.scan, tabBarIcon: ({ color }) => <Glyph ch="⌗" color={color} /> }} />
      <Tabs.Screen name="alerts" options={{ title: t.tabs.alerts, tabBarIcon: ({ color }) => <Glyph ch="⚠" color={color} /> }} />
      <Tabs.Screen name="history" options={{ title: t.tabs.history, tabBarIcon: ({ color }) => <Glyph ch="☰" color={color} /> }} />
    </Tabs>
  );
}

const st = StyleSheet.create({
  actions: { flexDirection: 'row', alignItems: 'center', gap: 4, marginEnd: 8 },
  action: { paddingHorizontal: 8, paddingVertical: 6 },
  actionText: { color: c.brand, fontWeight: '700', fontSize: 15 },
  gear: { color: c.brand, fontSize: 22 },
  title: { paddingVertical: 2 },
  titleText: { fontSize: 17, fontWeight: '700', color: c.text },
  statusRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  statusText: { fontSize: 12, fontWeight: '700' },
});
