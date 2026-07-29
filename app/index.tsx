import { Ionicons } from "@expo/vector-icons";
import { router } from "expo-router";
import {
  ImageBackground,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { BrandLockup, Button, Screen } from "../src/components/ui";
import { colors, radii, spacing } from "../src/theme";

export default function WelcomeScreen() {
  return (
    <Screen
      contentStyle={styles.content}
      bottom={
        <View style={styles.footer}>
          <Button
            icon="log-in-outline"
            label="登入帳號"
            onPress={() => router.push("/login")}
          />
          <Pressable
            onPress={() => router.replace("/(tabs)/home")}
            style={styles.browse}
          >
            <Text style={styles.browseText}>先逛逛本週產地選物</Text>
            <Ionicons color={colors.forest} name="arrow-forward" size={17} />
          </Pressable>
        </View>
      }
    >
      <ImageBackground
        imageStyle={styles.heroImage}
        source={require("../assets/products/rice.jpg")}
        style={styles.hero}
      >
        <View style={styles.overlay}>
          <BrandLockup light />
          <View>
            <Text style={styles.eyebrow}>FROM LAND TO TABLE</Text>
            <Text style={styles.headline}>十里產地，{"\n"}方圓日常。</Text>
            <Text style={styles.intro}>
              選購在地農產、參與共同購買，讓每一次消費都更靠近土地。
            </Text>
          </View>
        </View>
      </ImageBackground>

      <View style={styles.featureRow}>
        <View style={styles.feature}>
          <Ionicons color={colors.forest} name="leaf-outline" size={21} />
          <Text style={styles.featureTitle}>產地清楚</Text>
          <Text style={styles.featureBody}>認識食物從哪裡來</Text>
        </View>
        <View style={styles.rule} />
        <View style={styles.feature}>
          <Ionicons color={colors.forest} name="people-outline" size={22} />
          <Text style={styles.featureTitle}>共同購買</Text>
          <Text style={styles.featureBody}>一起累積需要的數量</Text>
        </View>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { paddingBottom: 0 },
  hero: { minHeight: 510 },
  heroImage: {
    borderBottomLeftRadius: 36,
    borderBottomRightRadius: 36,
  },
  overlay: {
    backgroundColor: "rgba(16,47,39,0.64)",
    borderBottomLeftRadius: 36,
    borderBottomRightRadius: 36,
    flex: 1,
    justifyContent: "space-between",
    minHeight: 510,
    padding: spacing.lg,
    paddingBottom: 42,
    paddingTop: 30,
  },
  eyebrow: {
    color: "#E7C9AA",
    fontSize: 10,
    fontWeight: "900",
    letterSpacing: 2.5,
  },
  headline: {
    color: colors.white,
    fontSize: 43,
    fontWeight: "900",
    letterSpacing: 1,
    lineHeight: 56,
    marginTop: 12,
  },
  intro: {
    color: "#F1ECE3",
    fontSize: 14,
    lineHeight: 23,
    marginTop: spacing.md,
    maxWidth: 320,
  },
  featureRow: {
    backgroundColor: colors.paper,
    borderRadius: radii.lg,
    flexDirection: "row",
    marginHorizontal: spacing.lg,
    marginTop: -18,
    padding: spacing.md,
  },
  feature: { flex: 1, gap: 4, paddingHorizontal: 8 },
  featureTitle: {
    color: colors.forest,
    fontSize: 13,
    fontWeight: "900",
  },
  featureBody: { color: colors.muted, fontSize: 10, lineHeight: 15 },
  rule: { backgroundColor: colors.line, width: 1 },
  footer: {
    backgroundColor: colors.cream,
    gap: 8,
    padding: spacing.lg,
    paddingBottom: spacing.lg,
  },
  browse: {
    alignItems: "center",
    flexDirection: "row",
    gap: 7,
    justifyContent: "center",
    padding: 11,
  },
  browseText: { color: colors.forest, fontSize: 13, fontWeight: "800" },
});
