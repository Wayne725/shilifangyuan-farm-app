import { router, useLocalSearchParams } from "expo-router";
import { useEffect } from "react";

import { LoadingState } from "../src/components/ui";

export default function LogisticsReturnScreen() {
  const params = useLocalSearchParams<{
    logistics?: string;
    order_id?: string;
  }>();

  useEffect(() => {
    if (!params.order_id) {
      router.replace("/(tabs)/orders");
      return;
    }
    router.replace({
      pathname: "/order/[id]",
      params: { id: params.order_id, logistics: params.logistics ?? "selected" },
    });
  }, [params.logistics, params.order_id]);

  return <LoadingState label="正在確認物流選擇" />;
}
