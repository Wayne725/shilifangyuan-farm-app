import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Stack } from "expo-router";
import { useState } from "react";
import { StatusBar } from "react-native";

import { AppViewport, LoadingState } from "../src/components/ui";
import { AuthProvider, useAuth } from "../src/store/AuthContext";
import { CartProvider } from "../src/store/CartContext";
import { WorkspaceProvider } from "../src/store/WorkspaceContext";
import { colors } from "../src/theme";

/** Holds the first frame until the stored session is read back, so a returning
 *  buyer never sees a logged-out flash after the ECPay redirect. */
function RestoreGate() {
  const { isRestoring } = useAuth();
  if (isRestoring) return <LoadingState label="載入中" />;
  return (
    <Stack
      screenOptions={{
        animation: "slide_from_right",
        contentStyle: { backgroundColor: colors.cream },
        headerShown: false,
      }}
    />
  );
}

export default function RootLayout() {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 20_000,
            retry: 1,
          },
          mutations: {
            retry: 0,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <WorkspaceProvider>
          <CartProvider>
            <AppViewport>
              <StatusBar
                backgroundColor={colors.cream}
                barStyle="dark-content"
              />
              <RestoreGate />
            </AppViewport>
          </CartProvider>
        </WorkspaceProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
