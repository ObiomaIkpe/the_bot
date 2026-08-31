import { Navigate, Route, Routes } from "react-router-dom";
import { AdminRoute } from "./auth/AdminRoute";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { Layout } from "./components/Layout";
import { RootRedirect } from "./components/RootRedirect";
import { AccountSettings } from "./pages/AccountSettings";
import { AdminAuditLog } from "./pages/admin/AdminAuditLog";
import { AdminEventFeed } from "./pages/admin/AdminEventFeed";
import { AdminModelConfigs } from "./pages/admin/AdminModelConfigs";
import { AdminSafetyChecks } from "./pages/admin/AdminSafetyChecks";
import { AdminTradeDetail } from "./pages/admin/AdminTradeDetail";
import { AdminTrades } from "./pages/admin/AdminTrades";
import { BrokerCredentials } from "./pages/BrokerCredentials";
import { Live } from "./pages/Live";
import { Login } from "./pages/Login";
import { ModelDetail } from "./pages/ModelDetail";
import { Models } from "./pages/Models";
import { Overview } from "./pages/Overview";
import { Profile } from "./pages/Profile";
import { TradeDetail } from "./pages/TradeDetail";
import { TradeHistory } from "./pages/TradeHistory";

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          element={
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          }
        >
          <Route path="/" element={<RootRedirect />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/models" element={<Models />} />
          <Route path="/models/:modelName" element={<ModelDetail />} />
          <Route path="/live" element={<Live />} />
          <Route path="/trades" element={<TradeHistory />} />
          <Route path="/trades/:tradeId" element={<TradeDetail />} />
          <Route path="/broker-credentials" element={<BrokerCredentials />} />
          <Route path="/account-settings" element={<AccountSettings />} />
          <Route path="/profile" element={<Profile />} />
          {/* Old routes from before this restructuring -- kept as
              redirects so no bookmarked/typed URL goes dead. */}
          <Route path="/dashboard" element={<Navigate to="/overview" replace />} />
          <Route path="/settings" element={<Navigate to="/account-settings" replace />} />

          {/* Replaces the separate Streamlit admin_dashboard/ tool --
              server-enforced by app/routers/admin.py's get_current_admin,
              this nested guard just keeps a non-admin from ever seeing
              the UI in the first place. */}
          <Route element={<AdminRoute />}>
            <Route path="/admin/events" element={<AdminEventFeed />} />
            <Route path="/admin/trades" element={<AdminTrades />} />
            <Route path="/admin/trades/:tradeId" element={<AdminTradeDetail />} />
            <Route path="/admin/safety-checks" element={<AdminSafetyChecks />} />
            <Route path="/admin/audit-log" element={<AdminAuditLog />} />
            <Route path="/admin/model-configs" element={<AdminModelConfigs />} />
          </Route>
        </Route>
        <Route
          path="*"
          element={
            <ProtectedRoute>
              <RootRedirect />
            </ProtectedRoute>
          }
        />
      </Routes>
    </AuthProvider>
  );
}

export default App;
