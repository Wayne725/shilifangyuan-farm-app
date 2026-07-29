import { router } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

import { InfoRow, PageHeader, Screen } from "../src/components/ui";
import { colors, radii, spacing } from "../src/theme";

export default function HelpScreen() {
  return (
    <Screen>
      <PageHeader onBack={() => router.back()} title="使用說明" />
      <View style={styles.content}>
        <Text style={styles.intro}>
          十里方圓提供一般選購與共同購買，所有訂單皆採合作社現場取貨。
        </Text>
        <View style={styles.panel}>
          <InfoRow
            icon="people-outline"
            label="團購投票"
            value="投票只用來表達需求，正式開團後仍須加入並付款。"
          />
          <View style={styles.rule} />
          <InfoRow
            icon="card-outline"
            label="線上付款"
            value="付款結果以訂單頁狀態為準，請勿重複關閉付款頁。"
          />
          <View style={styles.rule} />
          <InfoRow
            icon="storefront-outline"
            label="現場取貨"
            value="訂單顯示「可取貨」後，依通知時間至合作社取貨。"
          />
          <View style={styles.rule} />
          <InfoRow
            icon="document-text-outline"
            label="電子發票"
            value="完成取貨後開立，可使用綠界載具或手機條碼。"
          />
        </View>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { gap: 14, padding: spacing.md },
  intro: {
    color: colors.charcoal,
    fontSize: 14,
    lineHeight: 23,
  },
  panel: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: 13,
    padding: spacing.md,
  },
  rule: { backgroundColor: colors.line, height: 1 },
});
