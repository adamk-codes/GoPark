import AsyncStorage from '@react-native-async-storage/async-storage';
import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { Rewards } from '../api';
import { Badge, Card, ErrorLine, Muted, Section, s } from '../components/ui';
import { errorText, hhmm } from '../lib/format';
import { useApp } from '../lib/settings';
import { t } from '../strings';
import { c } from '../theme';

const CACHE_KEY = 'gopark.driver.rewards.v1'; // last good copy, shown when offline

// Points, weekly streak, perks and the points history for the driver (GET /users/{id}/rewards).
export default function RewardsScreen() {
  const { api, settings } = useApp();
  const userId = settings.driverUserId;
  const [data, setData] = useState<{ r: Rewards; mocked: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await api.rewards(userId);
      setData({ r: res.data, mocked: res.mocked });
      setError(null);
      AsyncStorage.setItem(`${CACHE_KEY}:${userId}`, JSON.stringify(res)).catch(() => undefined);
    } catch (e) {
      setError(errorText(e));
      const raw = await AsyncStorage.getItem(`${CACHE_KEY}:${userId}`).catch(() => null);
      if (raw) {
        const cached = JSON.parse(raw) as { data: Rewards; mocked: boolean };
        setData((d) => d ?? { r: cached.data, mocked: cached.mocked });
      }
    }
  }, [api, userId]);

  useFocusEffect(useCallback(() => {
    load();
  }, [load]));

  const refresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const r = data?.r;
  return (
    <ScrollView style={s.screen} contentContainerStyle={s.body}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={c.brand} />}>
      <Muted>{t.rewards.forUser(userId)}{data?.mocked ? ` · ${t.common.mock}` : ''}</Muted>
      {error && (r ? <Muted>{t.rewards.offline}</Muted> : <ErrorLine text={`${t.common.cantReach(settings.apiUrl)} · ${error}`} />)}
      {!r && !error && <Muted>{t.common.loading}</Muted>}
      {r && (
        <>
          <View style={s.row}>
            <Card style={s.flex}>
              <Text style={st.big}>{r.points}</Text>
              <Muted>{t.rewards.points}</Muted>
            </Card>
            <Card style={s.flex}>
              <Text style={st.big}>{r.streak_weeks}</Text>
              <Muted>{t.rewards.streak(r.streak_weeks)}</Muted>
            </Card>
          </View>

          <Section title={t.rewards.perks} />
          {r.perks.length === 0 ? <Muted>{t.rewards.noPerks}</Muted> : r.perks.map((p) => (
            <Card key={p.id}>
              <Badge label={p.label} fg={c.brand} bg={c.brandSoft} />
              {p.detail && <Text style={st.detail}>{p.detail}</Text>}
            </Card>
          ))}

          <Section title={t.rewards.history} />
          {r.history.length === 0 ? <Muted>{t.rewards.noHistory}</Muted> : (
            <Card style={{ padding: 0, gap: 0 }}>
              {r.history.map((h, i) => (
                <View key={i} style={st.row}>
                  <Text style={[s.mono, st.when]}>{h.at ? `${h.at.slice(5, 10)} ${hhmm(h.at)}` : ''}</Text>
                  <Text style={[st.reason, s.flex]}>{h.reason}</Text>
                  <Text style={[st.pts, { color: h.points >= 0 ? c.ok : c.bad }]}>{h.points >= 0 ? `+${h.points}` : h.points}</Text>
                </View>
              ))}
            </Card>
          )}
        </>
      )}
    </ScrollView>
  );
}

const st = StyleSheet.create({
  big: { fontSize: 34, fontWeight: '900', color: c.brand },
  detail: { fontSize: 15, color: c.text },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 12, paddingHorizontal: 14,
    borderBottomWidth: StyleSheet.hairlineWidth, borderColor: c.border },
  when: { width: 92 },
  reason: { fontSize: 15, color: c.text },
  pts: { fontSize: 16, fontWeight: '800', fontVariant: ['tabular-nums'] },
});
