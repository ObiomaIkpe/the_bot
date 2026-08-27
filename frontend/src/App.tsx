import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { Layout } from "./components/Layout";
import { RootRedirect } from "./components/RootRedirect";
import { AccountSettings } from "./pages/AccountSettings";
import { BrokerCredentials } from "./pages/BrokerCredentials";
import { Live } from "./pages/Live";
import { Login } from "./pages/Login";
import { ModelDetail } from "./pages/ModelDetail";
import { Models } from "./pages/Models";
import { Overview } from "./pages/Overview";
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
          <Route path="/broker-credentials" element={<BrokerCredentials />} />
          <Route path="/account-settings" element={<AccountSettings />} />
          {/* Old routes from before this restructuring -- kept as
              redirects so no bookmarked/typed URL goes dead. */}
          <Route path="/dashboard" element={<Navigate to="/overview" replace />} />
          <Route path="/settings" element={<Navigate to="/account-settings" replace />} />
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
