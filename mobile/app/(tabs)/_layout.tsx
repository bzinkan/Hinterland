import React from "react";
import FontAwesome from "@expo/vector-icons/FontAwesome";
import { Tabs } from "expo-router";
import { Platform } from "react-native";

import Colors from "@/constants/Colors";
import { useColorScheme } from "@/components/useColorScheme";
import { useClientOnlyValue } from "@/components/useClientOnlyValue";

function TabBarIcon(props: {
  name: React.ComponentProps<typeof FontAwesome>["name"];
  color: string;
}) {
  return <FontAwesome size={26} style={{ marginBottom: -3 }} {...props} />;
}

// Web is the adult-console surface only (per docs/mobile.md):
// review queue + my-observations summary + settings. The kid capture +
// dex + expedition flows are phone-first and stay hidden in the web
// nav. The route files for those surfaces still exist; web users can
// still navigate to them by URL but they shouldn't be discoverable.
const IS_WEB = Platform.OS === "web";

export default function TabLayout() {
  const colorScheme = useColorScheme();

  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: Colors[colorScheme ?? "light"].tint,
        headerShown: useClientOnlyValue(false, true),
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          // Header says the full name; the tab bar keeps the short one.
          title: "Field Journal",
          tabBarLabel: "Journal",
          tabBarIcon: ({ color }) => <TabBarIcon name="sticky-note" color={color} />,
        }}
      />
      <Tabs.Screen
        name="observe"
        options={{
          title: "Observe",
          tabBarIcon: ({ color }) => <TabBarIcon name="camera" color={color} />,
          href: IS_WEB ? null : "/observe",
        }}
      />
      <Tabs.Screen
        name="dex"
        options={{
          title: "Dex",
          tabBarIcon: ({ color }) => <TabBarIcon name="book" color={color} />,
          href: null,
        }}
      />
      <Tabs.Screen
        name="expeditions"
        options={{
          title: "Expeditions",
          tabBarIcon: ({ color }) => <TabBarIcon name="map" color={color} />,
          href: IS_WEB ? null : "/expeditions",
        }}
      />
      <Tabs.Screen
        name="sanctuary"
        options={{
          title: "Sanctuary",
          tabBarIcon: ({ color }) => <TabBarIcon name="leaf" color={color} />,
          href: IS_WEB ? null : "/sanctuary",
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: "Settings",
          tabBarIcon: ({ color }) => <TabBarIcon name="cog" color={color} />,
        }}
      />
    </Tabs>
  );
}
