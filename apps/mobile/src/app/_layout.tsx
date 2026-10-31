import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { SettingsProvider } from '../lib/settings';
import { t } from '../strings';
import { c } from '../theme';

export default function RootLayout() {
  return (
    <SettingsProvider>
      <Stack screenOptions={{ headerTintColor: c.brand, contentStyle: { backgroundColor: c.bg } }}>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="settings" options={{ title: t.settings.title }} />
        <Stack.Screen name="driver" options={{ title: t.driver.title }} />
      </Stack>
      <StatusBar style="dark" />
    </SettingsProvider>
  );
}
