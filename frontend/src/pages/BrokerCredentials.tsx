import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import type { BrokerCredentialCreate, BrokerCredentialOut } from "../api/types";
import { ConfirmModal } from "../components/ConfirmModal";

const thStyle: React.CSSProperties = { textAlign: "left", padding: "6px 10px", borderBottom: "2px solid #ddd" };
const tdStyle: React.CSSProperties = { padding: "6px 10px", borderBottom: "1px solid #eee" };

const emptyForm: BrokerCredentialCreate = {
  broker_name: "",
  account_login: "",
  account_password: "",
  server: "",
  account_type: "demo",
};

export function BrokerCredentials() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<BrokerCredentialCreate>(emptyForm);
  const [mintedToken, setMintedToken] = useState<string | null>(null);
  const [reMintTarget, setReMintTarget] = useState<BrokerCredentialOut | null>(null);

  const credentialsQuery = useQuery({
    queryKey: ["broker-credentials"],
    queryFn: () => apiClient.get<BrokerCredentialOut[]>("/broker-credentials"),
  });

  const createCredential = useMutation({
    mutationFn: (payload: BrokerCredentialCreate) => apiClient.post<BrokerCredentialOut>("/broker-credentials", payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["broker-credentials"] });
      setForm(emptyForm);
    },
  });

  const mintToken = useMutation({
    mutationFn: (credentialId: string) =>
      apiClient.post<{ bridge_token: string }>(`/broker-credentials/${credentialId}/bridge-token`),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["broker-credentials"] });
      setMintedToken(data.bridge_token);
      setReMintTarget(null);
    },
  });

  const toggleActive = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      apiClient.patch(`/broker-credentials/${id}`, { is_active }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["broker-credentials"] }),
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    createCredential.mutate(form);
  }

  function handleMintClick(cred: BrokerCredentialOut) {
    // Re-minting invalidates whatever token a currently-running bridge
    // worker holds -- that worker would fail its NEXT restart until
    // someone updates its BRIDGE_TOKEN env var. Only warn when a token
    // already exists; a first-ever mint has nothing to break.
    if (cred.bridge_configured) {
      setReMintTarget(cred);
    } else {
      mintToken.mutate(cred.credential_id);
    }
  }

  return (
    <div>
      <h1>Broker credentials</h1>
      <p style={{ color: "#666", maxWidth: 600 }}>
        Connect your MT5 account here. Saving credentials does not automatically start trading --
        a bridge worker still needs to be provisioned for this account (a manual, one-time setup
        step) before it shows up as connected on the Live page.
      </p>

      <h2>Add an account</h2>
      <form onSubmit={handleSubmit} style={{ maxWidth: 360, marginBottom: 32 }}>
        <div style={{ marginBottom: 10 }}>
          <label>
            Broker name
            <input
              type="text"
              required
              value={form.broker_name}
              onChange={(e) => setForm({ ...form, broker_name: e.target.value })}
              style={{ width: "100%", padding: 8, boxSizing: "border-box" }}
            />
          </label>
        </div>
        <div style={{ marginBottom: 10 }}>
          <label>
            MT5 account number
            <input
              type="text"
              required
              value={form.account_login}
              onChange={(e) => setForm({ ...form, account_login: e.target.value })}
              style={{ width: "100%", padding: 8, boxSizing: "border-box" }}
            />
          </label>
        </div>
        <div style={{ marginBottom: 10 }}>
          <label>
            MT5 password
            <input
              type="password"
              required
              value={form.account_password}
              onChange={(e) => setForm({ ...form, account_password: e.target.value })}
              style={{ width: "100%", padding: 8, boxSizing: "border-box" }}
            />
          </label>
        </div>
        <div style={{ marginBottom: 10 }}>
          <label>
            Server
            <input
              type="text"
              required
              placeholder="e.g. Exness-MT5Trial9"
              value={form.server}
              onChange={(e) => setForm({ ...form, server: e.target.value })}
              style={{ width: "100%", padding: 8, boxSizing: "border-box" }}
            />
          </label>
        </div>
        <div style={{ marginBottom: 10 }}>
          <label>
            Account type
            <select
              value={form.account_type}
              onChange={(e) => setForm({ ...form, account_type: e.target.value as "demo" | "live" })}
              style={{ width: "100%", padding: 8 }}
            >
              <option value="demo">demo</option>
              <option value="live">live</option>
            </select>
          </label>
        </div>
        {createCredential.error && <p style={{ color: "crimson" }}>{String(createCredential.error)}</p>}
        <button type="submit" disabled={createCredential.isPending} style={{ padding: "8px 16px" }}>
          {createCredential.isPending ? "Saving..." : "Save credentials"}
        </button>
      </form>

      <h2>Your accounts</h2>
      {credentialsQuery.isLoading && <p>Loading...</p>}
      {credentialsQuery.error && <p style={{ color: "crimson" }}>Failed to load: {String(credentialsQuery.error)}</p>}
      {credentialsQuery.data && (
        <table style={{ borderCollapse: "collapse", width: "100%", maxWidth: 800 }}>
          <thead>
            <tr>
              <th style={thStyle}>Broker</th>
              <th style={thStyle}>Account</th>
              <th style={thStyle}>Server</th>
              <th style={thStyle}>Type</th>
              <th style={thStyle}>Active</th>
              <th style={thStyle}>Bridge connected</th>
              <th style={thStyle}></th>
            </tr>
          </thead>
          <tbody>
            {credentialsQuery.data.length === 0 && (
              <tr>
                <td style={tdStyle} colSpan={7}>
                  No accounts connected yet.
                </td>
              </tr>
            )}
            {credentialsQuery.data.map((cred) => (
              <tr key={cred.credential_id}>
                <td style={tdStyle}>{cred.broker_name}</td>
                <td style={tdStyle}>{cred.account_login}</td>
                <td style={tdStyle}>{cred.server}</td>
                <td style={tdStyle}>{cred.account_type}</td>
                <td style={tdStyle}>
                  <input
                    type="checkbox"
                    checked={cred.is_active}
                    disabled={toggleActive.isPending}
                    onChange={(e) => toggleActive.mutate({ id: cred.credential_id, is_active: e.target.checked })}
                  />
                </td>
                <td style={tdStyle}>{cred.bridge_configured ? "yes" : "not yet"}</td>
                <td style={tdStyle}>
                  <button type="button" disabled={mintToken.isPending} onClick={() => handleMintClick(cred)}>
                    {cred.bridge_configured ? "Re-mint bridge token" : "Mint bridge token"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {mintedToken && (
        <ConfirmModal
          title="Bridge token minted"
          message={`Copy this now -- it won't be shown again: ${mintedToken}`}
          confirmLabel="I've copied it"
          onConfirm={() => setMintedToken(null)}
          onCancel={() => setMintedToken(null)}
        />
      )}

      {reMintTarget && (
        <ConfirmModal
          title="Re-mint bridge token?"
          message="This invalidates the current token immediately. If a bridge worker for this account is already running, it will keep working until its next restart, then fail to start until you update its BRIDGE_TOKEN with the new value."
          confirmLabel="Re-mint anyway"
          busy={mintToken.isPending}
          onCancel={() => setReMintTarget(null)}
          onConfirm={() => mintToken.mutate(reMintTarget.credential_id)}
        />
      )}
    </div>
  );
}
