// Optional background arrival sensing: a geofence around each lot the driver has a booking at. Enter/exit
// become `geofence` signals for fusion (via the driver outbox, so they survive no signal underground).
// Needs a development build: Expo Go has no background location or TaskManager on Android, and no
// background execution on iOS (Expo SDK 58 docs). Imported from the root layout so the task is defined
// at module load, before any navigation, as TaskManager requires.
import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import { Platform } from 'react-native';
import { loadDriver, queueGeofence, type DriverConfig } from './driver';

export const GEOFENCE_TASK = 'gopark-geofence';
const MAX_REGIONS = 20; // iOS limit (Android allows 100)

async function onGeofence({ data, error }: TaskManager.TaskManagerTaskBody<{
  eventType: Location.GeofencingEventType; region: Location.LocationRegion }>) {
  if (error || !data?.region?.identifier) return;
  const cfg = await loadDriver();
  if (!cfg?.sensing) return;
  const kind = data.eventType === Location.GeofencingEventType.Enter ? 'enter'
    : data.eventType === Location.GeofencingEventType.Exit ? 'exit' : null;
  if (!kind) return;
  try {
    await queueGeofence(cfg, data.region.identifier, kind, data.region.radius);
  } catch {
    // Queued or not, never crash a background task; the next foreground flush retries.
  }
}

// Guarded: this module loads at app start, and the attendant app must never fail to open (e.g. in Expo Go).
try {
  TaskManager.defineTask(GEOFENCE_TASK, onGeofence);
} catch (e) {
  console.warn('Geofence task not defined (background sensing unavailable here)', e);
}

export type SensingSupport = 'ok' | 'needs_dev_build' | 'web';

export async function sensingSupport(): Promise<SensingSupport> {
  if (Platform.OS === 'web') return 'web';
  try {
    return (await TaskManager.isAvailableAsync()) ? 'ok' : 'needs_dev_build';
  } catch {
    return 'needs_dev_build';
  }
}

// Lots with a booking that hasn't ended yet, one region each (identifier = lot id).
export function regionsFor(cfg: DriverConfig, nowMs: number): Location.LocationRegion[] {
  const lotIds = new Set(cfg.bookings.filter((b) => Date.parse(b.end) > nowMs).map((b) => b.lot_id));
  return cfg.lots.filter((l) => lotIds.has(l.id)).slice(0, MAX_REGIONS).map((l) => ({
    identifier: l.id, latitude: l.lat, longitude: l.lng, radius: Math.max(100, l.radius_m),
    notifyOnEnter: true, notifyOnExit: true,
  }));
}

export type StartResult = 'started' | 'no_permission' | 'no_background_permission' | 'no_regions' | SensingSupport;

export async function startSensing(cfg: DriverConfig, nowMs: number): Promise<StartResult> {
  const support = await sensingSupport();
  if (support !== 'ok') return support;
  const regions = regionsFor(cfg, nowMs);
  if (!regions.length) return 'no_regions';
  const fg = await Location.requestForegroundPermissionsAsync();
  if (!fg.granted) return 'no_permission';
  // Foreground first, then "Always" (Android 11+ sends the driver to system settings for this).
  const bg = await Location.requestBackgroundPermissionsAsync();
  if (!bg.granted) return 'no_background_permission';
  await Location.startGeofencingAsync(GEOFENCE_TASK, regions); // calling again replaces the regions
  return 'started';
}

export async function stopSensing() {
  try {
    if (await Location.hasStartedGeofencingAsync(GEOFENCE_TASK)) await Location.stopGeofencingAsync(GEOFENCE_TASK);
  } catch {
    // Not available here (Expo Go / web): nothing was started.
  }
}

export async function isSensing(): Promise<boolean> {
  try {
    return await Location.hasStartedGeofencingAsync(GEOFENCE_TASK);
  } catch {
    return false;
  }
}
