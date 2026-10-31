import { router } from 'expo-router';
import { Tabs } from 'expo-router/js-tabs';
import { Pressable, StyleSheet, Text, View, type ColorValue } from 'react-native';
import { useApp } from '../../lib/settings';
import { t } from '../../strings';
import { c } from '../../theme';

// Glyph icons: no icon font to load, and they work in plain Expo Go.
function Glyph({ ch, color }: { ch: string; color: ColorValue }) {
  return <Text style={{ fontSize: 22, color }}>{ch}</Text>;
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
        headerTitle: `${t.appName} · ${settings.lotId}`,
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
});
