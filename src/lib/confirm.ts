import { Alert, Platform } from "react-native";

/**
 * Asks before an irreversible action. `Alert` is a no-op on react-native-web,
 * so the Web build falls back to the native browser dialog.
 */
export function confirmAction({
  title,
  message,
  confirmLabel = "確認",
  cancelLabel = "取消",
  destructive = true,
}: {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
}): Promise<boolean> {
  if (Platform.OS === "web") {
    return Promise.resolve(
      globalThis.confirm?.(`${title}\n\n${message}`) ?? true,
    );
  }
  return new Promise((resolve) => {
    Alert.alert(title, message, [
      { text: cancelLabel, style: "cancel", onPress: () => resolve(false) },
      {
        text: confirmLabel,
        style: destructive ? "destructive" : "default",
        onPress: () => resolve(true),
      },
    ]);
  });
}
