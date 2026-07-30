import { Ionicons } from "@expo/vector-icons";
import { Tabs } from "expo-router";
import type { ComponentProps } from "react";
import { StyleSheet, View } from "react-native";

import { WorkspaceTopBar } from "../../src/components/ui";
import { useWorkspace } from "../../src/store/WorkspaceContext";
import { colors } from "../../src/theme";

type IconName = ComponentProps<typeof Ionicons>["name"];

const icons: Record<
  string,
  { active: IconName; inactive: IconName }
> = {
  home: { active: "home", inactive: "home-outline" },
  "group-buy": { active: "people", inactive: "people-outline" },
  meals: { active: "restaurant", inactive: "restaurant-outline" },
  orders: { active: "receipt", inactive: "receipt-outline" },
  cart: { active: "basket", inactive: "basket-outline" },
  profile: { active: "person", inactive: "person-outline" },
  "social-home": { active: "home", inactive: "home-outline" },
  members: { active: "id-card", inactive: "id-card-outline" },
  activities: { active: "calendar", inactive: "calendar-outline" },
  "member-proposals": {
    active: "chatbubbles",
    inactive: "chatbubbles-outline",
  },
  "social-profile": { active: "person", inactive: "person-outline" },
};

export default function TabLayout() {
  const { workspace } = useWorkspace();
  const life = workspace === "life";

  return (
    <View style={styles.layout}>
      <WorkspaceTopBar />
      <Tabs
        screenOptions={({ route }) => ({
          headerShown: false,
          sceneStyle: { backgroundColor: colors.cream },
          tabBarActiveTintColor: colors.forest,
          tabBarInactiveTintColor: colors.muted,
          tabBarIcon: ({ focused, color }) => {
            const pair = icons[route.name] ?? icons.home!;
            return (
              <Ionicons
                color={color}
                name={focused ? pair.active : pair.inactive}
                size={22}
              />
            );
          },
          tabBarLabelStyle: styles.label,
          tabBarStyle: styles.bar,
        })}
      >
        <Tabs.Screen
          name="home"
          options={{ href: life ? "/home" : null, title: "首頁" }}
        />
        <Tabs.Screen
          name="group-buy"
          options={{ href: life ? "/group-buy" : null, title: "團購" }}
        />
        <Tabs.Screen
          name="meals"
          options={{ href: life ? "/meals" : null, title: "便當" }}
        />
        <Tabs.Screen
          name="orders"
          options={{ href: life ? "/orders" : null, title: "訂單" }}
        />
        <Tabs.Screen
          name="profile"
          options={{ href: life ? "/profile" : null, title: "我的" }}
        />
        <Tabs.Screen name="cart" options={{ href: null, title: "購物車" }} />
        <Tabs.Screen
          name="social-home"
          options={{ href: life ? null : "/social-home", title: "社務首頁" }}
        />
        <Tabs.Screen
          name="members"
          options={{ href: life ? null : "/members", title: "社員" }}
        />
        <Tabs.Screen
          name="activities"
          options={{ href: life ? null : "/activities", title: "活動" }}
        />
        <Tabs.Screen
          name="member-proposals"
          options={{ href: life ? null : "/member-proposals", title: "提案" }}
        />
        <Tabs.Screen
          name="social-profile"
          options={{ href: life ? null : "/social-profile", title: "我的" }}
        />
      </Tabs>
    </View>
  );
}

const styles = StyleSheet.create({
  layout: { flex: 1 },
  bar: {
    backgroundColor: colors.paper,
    borderTopColor: colors.line,
    height: 72,
    paddingBottom: 8,
    paddingTop: 7,
  },
  label: { fontSize: 12, fontWeight: "800" },
});
