import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AuthProvider } from "./context/AuthContext";
import { CommerceProvider } from "./context/CommerceContext";
import { router } from "./router";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <CommerceProvider>
          <RouterProvider router={router} />
        </CommerceProvider>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
