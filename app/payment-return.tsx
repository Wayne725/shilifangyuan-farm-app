import { router, useLocalSearchParams } from "expo-router";
import { useEffect } from "react";

import { LoadingState } from "../src/components/ui";

export default function PaymentReturnScreen() {
  const params = useLocalSearchParams<{
    membership_charge_id?: string;
    order_id?: string;
    payment?: string;
  }>();

  useEffect(() => {
    if (params.order_id) {
      router.replace({
        pathname: "/order/[id]",
        params: { id: params.order_id, payment: params.payment ?? "confirming" },
      });
      return;
    }
    router.replace({
      pathname: "/(tabs)/members",
      params: {
        membership_charge_id: params.membership_charge_id,
        payment: params.payment ?? "confirming",
      },
    });
  }, [params.membership_charge_id, params.order_id, params.payment]);

  return <LoadingState label="正在確認付款結果" />;
}
