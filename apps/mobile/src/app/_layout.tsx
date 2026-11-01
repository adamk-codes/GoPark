// Defines the background geofence task at module load (TaskManager needs it before any navigation runs).
import '../lib/geofence';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { SettingsProvider } from '../lib/settings';
import { SyncProvider } from '../lib/sync';
import { t } from '../strings';
import { c } from '../theme';

export default function RootLayout() {
  return (
    <SettingsProvider>
      <SyncProvider>
        <Stack screenOptions={{ headerTintColor: c.brand, contentStyle: { backgroundColor: c.bg } }}>
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          <Stack.Screen name="settings" options={{ title: t.settings.title }} />
          <Stack.Screen name="driver" options={{ title: t.driver.title }} />
          <Stack.Screen name="rewards" options={{ title: t.rewards.title }} />
        </Stack>
      </SyncProvider>
      <StatusBar style="dark" />
    </SettingsProvider>
  );
}
