import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import type { BrokerCredentialCreate, BrokerCredentialOut } from "../api/types";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ConfirmModal } from "../components/ConfirmModal";
import { Table } from "../components/Table";

const emptyForm: BrokerCredentialCreate = {
  broker_name: "",
  account_login: "",
  account_password: "",
  server: "",
  account_type: "demo",
};

function ConnectForm({
  form,
  setForm,
  onSubmit,
  submitting,
  error,
}: {
  form: BrokerCredentialCreate;
  setForm: (form: BrokerCredentialCreate) => void;
  onSubmit: (e: FormEvent) => void;
  submitting: boolean;
  error: unknown;
}) {
  return (
    <form onSubmit={onSubmit} style={{ maxWidth: 360 }}>
      <div className="field">
        <label>Broker name</label>
        <input
          type="text"
          required
          value={form.broker_name}
          onChange={(e) => setForm({ ...form, broker_name: e.target.value })}
        />
      </div>
      <div className="field">
        <label>MT5 account number</label>
        <input
          type="text"
          required
          value={form.account_login}
          onChange={(e) => setForm({ ...form, account_login: e.target.value })}
        />
      </div>
      <div className="field">
        <label>MT5 password</label>
        <input
          type="password"
          required
          value={form.account_password}
          onChange={(e) => setForm({ ...form, account_password: e.target.value })}
        />
      </div>
      <div className="field">
        <label>Server</label>
        <input
          type="text"
          required
          placeholder="e.g. Exness-MT5Trial9"
          value={form.server}
          onChange={(e) => setForm({ ...form, server: e.target.value })}
        />
      </div>
      <div className="field">
        <label>Account type</label>
        <select
          value={form.account_type}
          onChange={(e) => setForm({ ...form, account_type: e.target.value as "demo" | "live" })}
        >
          <option value="demo">demo</option>
          <option value="live">live</option>
        </select>
      </div>
      {error != null && <p style={{ color: "var(--negative)" }}>{String(error)}</p>}
      <Button type="submit" variant="primary" disabled={submitting}>
        {submitting ? "Saving..." : "Save credentials"}
      </Button>
    </form>
  );
}

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

  const hasAccounts = (credentialsQuery.data?.length ?? 0) > 0;

  return (
    <div>
      <div className="page-header">
        <h1>Broker connection</h1>
      </div>

      {!credentialsQuery.isLoading && !hasAccounts && (
        // First-run: no accounts connected yet, so the connect form is
        // the whole point of this page rather than a buried nav item.
        <Card style={{ maxWidth: 480, marginBottom: 32 }}>
          <h2 style={{ marginTop: 0 }}>Connect your MT5 account to get started</h2>
          <p style={{ color: "var(--text-muted)" }}>
            Saving credentials does not automatically start trading — a bridge worker still needs to
            be provisioned for this account (a one-time operator setup step) before it shows up as
            connected on the Live page.
          </p>
          <ConnectForm
            form={form}
            setForm={setForm}
            onSubmit={handleSubmit}
            submitting={createCredential.isPending}
            error={createCredential.error}
          />
        </Card>
      )}

      {hasAccounts && (
        <>
          <Card style={{ maxWidth: 480, marginBottom: 32 }}>
            <h2 style={{ marginTop: 0 }}>Add another account</h2>
            <ConnectForm
              form={form}
              setForm={setForm}
              onSubmit={handleSubmit}
              submitting={createCredential.isPending}
              error={createCredential.error}
            />
          </Card>

          <h2 className="section-title">Your accounts</h2>
          {credentialsQuery.error && (
            <p style={{ color: "var(--negative)" }}>Failed to load: {String(credentialsQuery.error)}</p>
          )}
          <Table>
            <thead>
              <tr>
                <th>Broker</th>
                <th>Account</th>
                <th>Server</th>
                <th>Type</th>
                <th>Active</th>
                <th>Bridge connected</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {credentialsQuery.data!.map((cred) => (
                <tr key={cred.credential_id}>
                  <td>{cred.broker_name}</td>
                  <td>{cred.account_login}</td>
                  <td>{cred.server}</td>
                  <td>{cred.account_type}</td>
                  <td>
                    <input
                      type="checkbox"
                      checked={cred.is_active}
                      disabled={toggleActive.isPending}
                      onChange={(e) => toggleActive.mutate({ id: cred.credential_id, is_active: e.target.checked })}
                    />
                  </td>
                  <td>{cred.bridge_configured ? "yes" : "not yet"}</td>
                  <td>
                    <Button variant="secondary" disabled={mintToken.isPending} onClick={() => handleMintClick(cred)}>
                      {cred.bridge_configured ? "Re-mint bridge token" : "Mint bridge token"}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}

      {mintedToken && (
        <ConfirmModal
          title="Bridge token minted"
          message={`Copy this now -- it won't be shown again: ${mintedToken}`}
          confirmLabel="I've copied it"
          variant="primary"
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
