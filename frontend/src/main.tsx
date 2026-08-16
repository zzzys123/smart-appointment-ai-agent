import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import AppErrorBoundary from "./components/AppErrorBoundary";
import "./styles.css";

const basename = import.meta.env.BASE_URL.replace(/\/$/, "");
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      retry: 1,
      refetchOnWindowFocus: false
    },
    mutations: { retry: 0 }
  }
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: "#635bff",
          colorInfo: "#4f7cff",
          borderRadius: 12,
          fontFamily: "Inter, PingFang SC, Microsoft YaHei, sans-serif"
        }
      }}
    >
      <QueryClientProvider client={queryClient}>
        <BrowserRouter basename={basename}>
          <AppErrorBoundary>
            <App />
          </AppErrorBoundary>
        </BrowserRouter>
      </QueryClientProvider>
    </ConfigProvider>
  </React.StrictMode>
);
