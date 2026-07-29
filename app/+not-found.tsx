import { router } from "expo-router";

import { EmptyState, PageHeader, Screen } from "../src/components/ui";

export default function NotFoundScreen() {
  return (
    <Screen>
      <PageHeader onBack={() => router.back()} title="找不到頁面" />
      <EmptyState
        action="回到首頁"
        description="這個連結可能已失效，請回到首頁繼續瀏覽。"
        icon="map-outline"
        onAction={() => router.replace("/(tabs)/home")}
        title="這裡沒有內容"
      />
    </Screen>
  );
}
