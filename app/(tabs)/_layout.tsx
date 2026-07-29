import { Ionicons } from "@expo/vector-icons";
import { Tabs } from "expo-router";
import type { ComponentProps } from "react";
import { StyleSheet } from "react-native";

import { useCart } from "../../src/store/CartContext";
import { colors } from "../../src/theme";

type IconName = ComponentProps<typeof Ionicons>["name"];

const icons: Record<
  string,
  { active: IconName; inactive: IconName }
> = {
  home: { active: "home", inactive: "home-outline" },
  "group-buy": { active: "people", inactive: "people-outline" },
  orders: { active: "receipt", inactive: "receipt-outline" },
  cart: { active: "basket", inactive: "basket-outline" },
  profile: { active: "person", inactive: "person-outline" },
};

export default function TabLayout() {
  const { itemCount } = useCart();

  return (
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
      <Tabs.Screen name="home" options={{ title: "首頁" }} />
      <Tabs.Screen name="group-buy" options={{ title: "團購" }} />
      <Tabs.Screen name="orders" options={{ title: "訂單" }} />
      <Tabs.Screen
        name="cart"
        options={{
          title: "購物車",
          tabBarBadge: itemCount > 0 ? Math.min(itemCount, 99) : undefined,
          tabBarBadgeStyle: styles.badge,
        }}
      />
      <Tabs.Screen name="profile" options={{ title: "我的" }} />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  bar: {
    backgroundColor: colors.paper,
    borderTopColor: colors.line,
    height: 72,
    paddingBottom: 8,
    paddingTop: 7,
  },
  label: { fontSize: 10, fontWeight: "800" },
  badge: {
    backgroundColor: colors.orange,
    color: colors.white,
    fontSize: 9,
  },
});
