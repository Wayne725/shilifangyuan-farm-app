import { Ionicons } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";

import {
  EmptyState,
  LoadingState,
  PageHeader,
  Screen,
} from "../src/components/ui";
import { dateTime } from "../src/lib/format";
import { api } from "../src/services/api";
import { colors, radii, spacing } from "../src/theme";
import type { AppNotification } from "../src/types";

const noticeIcons: Record<
  AppNotification["kind"],
  keyof typeof Ionicons.glyphMap
> = {
  proposal: "chatbubbles-outline",
  group: "people-outline",
  payment: "card-outline",
  pickup: "storefront-outline",
  invoice: "document-text-outline",
};

export default function NotificationsScreen() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["notifications"],
    queryFn: api.notifications,
  });
  const markRead = useMutation({
    mutationFn: (id: string) => api.markNotificationRead(id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const openNotice = (notice: AppNotification) => {
    if (!notice.read_at) markRead.mutate(notice.id);
    if (notice.route) router.push(notice.route as never);
  };

  return (
    <Screen>
      <PageHeader
        onBack={() => router.back()}
        subtitle="團購、付款、取貨與發票的重要進度都會保留在這裡。"
        title="通知中心"
      />
      {query.isLoading ? (
        <LoadingState label="載入通知" />
      ) : query.data?.length ? (
        <View style={styles.list}>
          {query.data.map((notice) => (
            <Pressable
              key={notice.id}
              onPress={() => openNotice(notice)}
              style={({ pressed }) => [
                styles.notice,
                !notice.read_at && styles.noticeUnread,
                pressed && styles.pressed,
              ]}
            >
              <View style={styles.icon}>
                <Ionicons
                  color={colors.forest}
                  name={noticeIcons[notice.kind]}
                  size={21}
                />
              </View>
              <View style={styles.copy}>
                <View style={styles.titleRow}>
                  <Text style={styles.title}>{notice.title}</Text>
                  {!notice.read_at ? <View style={styles.dot} /> : null}
                </View>
                <Text style={styles.body}>{notice.body}</Text>
                <Text style={styles.date}>{dateTime(notice.created_at)}</Text>
              </View>
              {notice.route ? (
                <Ionicons
                  color={colors.sage}
                  name="chevron-forward"
                  size={18}
                />
              ) : null}
            </Pressable>
          ))}
        </View>
      ) : (
        <EmptyState
          description="有新的團購、付款或取貨進度時會顯示在這裡。"
          icon="notifications-outline"
          title="目前沒有通知"
        />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  list: { gap: 9, padding: spacing.md },
  notice: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    flexDirection: "row",
    padding: 13,
  },
  noticeUnread: { backgroundColor: colors.sageLight },
  icon: {
    alignItems: "center",
    backgroundColor: colors.paper,
    borderRadius: 13,
    height: 43,
    justifyContent: "center",
    width: 43,
  },
  copy: { flex: 1, marginLeft: 11, marginRight: 5 },
  titleRow: { alignItems: "center", flexDirection: "row" },
  title: { color: colors.forest, flex: 1, fontSize: 13, fontWeight: "900" },
  dot: {
    backgroundColor: colors.orange,
    borderRadius: 4,
    height: 7,
    marginLeft: 6,
    width: 7,
  },
  body: {
    color: colors.charcoal,
    fontSize: 11,
    lineHeight: 17,
    marginTop: 4,
  },
  date: { color: colors.muted, fontSize: 9, marginTop: 6 },
  pressed: { opacity: 0.7 },
});
