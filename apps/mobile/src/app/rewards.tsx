import AsyncStorage from '@react-native-async-storage/async-storage';
import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { Rewards } from '../contract';
import { Badge, Card, ErrorLine, Muted, Section, s } from '../components/ui';
import { errorText, hhmm } from '../lib/format';
import { useApp } from '../lib/settings';
import { t } from '../strings';
import { c } from '../theme';

const CACHE_KEY = 'gopark.driver.rewards.v2'; // last good copy, shown when offline

// Points, weekly streak, grace minutes, tier, perks and the points history (GET /users/{id}/rewards).
export default function RewardsScreen() {
  const { api, settings } = useApp();
  const userId = settings.driverUserId;
  const [r, setR] = useState<Rewards | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await api.rewards(userId);
      setR(res);
      setError(null);
      AsyncStorage.setItem(`${CACHE_KEY}:${userId}`, JSON.stringify(res)).catch(() => undefined);
    } catch (e) {
      setError(errorText(e));
      const raw = await AsyncStorage.getItem(`${CACHE_KEY}:${userId}`).catch(() => null);
      if (raw) setR((cur) => cur ?? (JSON.parse(raw) as Rewards));
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

  return (
    <ScrollView style={s.screen} contentContainerStyle={s.body}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={c.brand} />}>
      <Muted>{t.rewards.forUser(userId)}</Muted>
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
          <View style={s.row}>
            <Card style={s.flex}>
              <Text style={st.mid}>{r.grace_minutes}</Text>
              <Muted>{t.rewards.grace}</Muted>
            </Card>
            <Card style={s.flex}>
              <Badge label={t.rewards.tiers[r.tier] ?? r.tier} fg={r.tier === 'verified' ? c.ok : c.muted}
                bg={r.tier === 'verified' ? c.okSoft : c.border} />
              <Muted>{t.rewards.tierHelp[r.tier] ?? ''}</Muted>
            </Card>
          </View>

          <Section title={t.rewards.perks} />
          {r.perks.length === 0 ? <Muted>{t.rewards.noPerks}</Muted> : r.perks.map((p) => (
            <Card key={p.id} style={!p.unlocked && st.locked}>
              <View style={s.row}>
                <Text style={[st.perk, s.flex]}>{p.name}</Text>
                <Badge label={p.unlocked ? t.rewards.unlocked : t.rewards.locked}
                  fg={p.unlocked ? c.brand : c.muted} bg={p.unlocked ? c.brandSoft : c.border} />
              </View>
              <Text style={st.detail}>{p.description}</Text>
            </Card>
          ))}

          <Section title={t.rewards.history} />
          {r.history.length === 0 ? <Muted>{t.rewards.noHistory}</Muted> : (
            <Card style={{ padding: 0, gap: 0 }}>
              {r.history.map((h, i) => (
                <View key={i} style={st.row}>
                  <Text style={[s.mono, st.when]}>{`${h.at.slice(5, 10)} ${hhmm(h.at)}`}</Text>
                  <Text style={[st.reason, s.flex]}>{h.label}</Text>
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
  mid: { fontSize: 24, fontWeight: '800', color: c.text },
  perk: { fontSize: 16, fontWeight: '800', color: c.text },
  locked: { opacity: 0.6 },
  detail: { fontSize: 15, color: c.text },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 12, paddingHorizontal: 14,
    borderBottomWidth: StyleSheet.hairlineWidth, borderColor: c.border },
  when: { width: 92 },
  reason: { fontSize: 15, color: c.text },
  pts: { fontSize: 16, fontWeight: '800', fontVariant: ['tabular-nums'] },
});
