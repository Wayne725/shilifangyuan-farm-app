import * as WebBrowser from "expo-web-browser";
import { Platform } from "react-native";

/**
 * Sends the buyer to the ECPay checkout page.
 *
 * On web this is a full navigation because ECPay needs a top-level form POST,
 * so the session must survive the round trip (see the persisted AuthContext).
 * On native we keep the app alive and open an in-app browser instead.
 */
export async function openPaymentPage(paymentUrl: string) {
  if (Platform.OS === "web") {
    window.location.assign(paymentUrl);
    return;
  }
  await WebBrowser.openBrowserAsync(paymentUrl);
}
