import * as WebBrowser from "expo-web-browser";
import { Platform } from "react-native";

type HostedFlow = "payment" | "logistics";

const returnUrls: Record<HostedFlow, string> = {
  logistics: "shilifangyuan://logistics-return",
  payment: "shilifangyuan://payment-return",
};

function nativeFlowUrl(url: string) {
  return `${url}${url.includes("?") ? "&" : "?"}client=native`;
}

async function openHostedFlow(url: string, flow: HostedFlow) {
  if (Platform.OS === "web") {
    window.location.assign(url);
    return;
  }
  await WebBrowser.openAuthSessionAsync(
    nativeFlowUrl(url),
    returnUrls[flow],
  );
}

export function openPaymentPage(paymentUrl: string) {
  return openHostedFlow(paymentUrl, "payment");
}

export function openLogisticsPage(logisticsUrl: string) {
  return openHostedFlow(logisticsUrl, "logistics");
}

export async function openExternalPage(url: string) {
  if (Platform.OS === "web") {
    window.location.assign(url);
    return;
  }
  await WebBrowser.openBrowserAsync(url);
}
