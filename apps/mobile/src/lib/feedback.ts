import { useAudioPlayer } from 'expo-audio';
import * as Haptics from 'expo-haptics';
import { useCallback } from 'react';
import type { Tone } from '../theme';

const haptic: Record<Tone, Haptics.NotificationFeedbackType> = {
  ok: Haptics.NotificationFeedbackType.Success,
  warn: Haptics.NotificationFeedbackType.Warning,
  bad: Haptics.NotificationFeedbackType.Error,
  info: Haptics.NotificationFeedbackType.Success,
};

// Haptic + short beep per scan outcome, so the attendant can keep eyes on the car.
export function useScanFeedback() {
  const ok = useAudioPlayer(require('../../assets/sounds/ok.wav'));
  const warn = useAudioPlayer(require('../../assets/sounds/warn.wav'));
  const bad = useAudioPlayer(require('../../assets/sounds/error.wav'));

  return useCallback((tone: Tone) => {
    Haptics.notificationAsync(haptic[tone]).catch(() => undefined);
    const player = tone === 'bad' ? bad : tone === 'warn' ? warn : ok;
    try {
      player.seekTo(0).catch(() => undefined);
      player.play();
    } catch {
      // Sound is a nice-to-have; never let it break a scan.
    }
  }, [ok, warn, bad]);
}
