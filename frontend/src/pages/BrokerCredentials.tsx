import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import type { BrokerCredentialCreate, BrokerCredentialOut } from "../api/types";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ConfirmModal } from "../components/ConfirmModal";
import { Table } from "../components/Table";
import { provisioningBadgeVariant, provisioningStepLabel } from "../lib/provisioningStatus";

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
    // autoComplete="off" on the form + explicit non-standard name/
    // autoComplete on every field below: a real incident, not just
    // tidiness -- browser autofill once put a saved login EMAIL into
    // "MT5 account number" (a plain text field immediately followed by
    // a password field reads as a username/password pair to Chrome's
    // heuristics regardless of label text), and the resulting bad
    // broker_credentials row had to be found and deleted by hand.
    <form onSubmit={onSubmit} className="max-w-[360px]" autoComplete="off">
      <div className="flex flex-col gap-1 mb-3">
        <label className="text-[13px] text-text-muted">Broker name</label>
        <input
          type="text"
          name="mt5-broker-name"
          autoComplete="off"
          required
          value={form.broker_name}
          onChange={(e) => setForm({ ...form, broker_name: e.target.value })}
        />
      </div>
      <div className="flex flex-col gap-1 mb-3">
        <label className="text-[13px] text-text-muted">MT5 account number</label>
        <input
          type="text"
          name="mt5-account-login"
          autoComplete="off"
          required
          value={form.account_login}
          onChange={(e) => setForm({ ...form, account_login: e.target.value })}
        />
      </div>
      <div className="flex flex-col gap-1 mb-3">
        <label className="text-[13px] text-text-muted">MT5 password</label>
        <input
          type="password"
          name="mt5-account-password"
          autoComplete="new-password"
          required
          value={form.account_password}
          onChange={(e) => setForm({ ...form, account_password: e.target.value })}
        />
      </div>
      <div className="flex flex-col gap-1 mb-3">
        <label className="text-[13px] text-text-muted">Server</label>
        <input
          type="text"
          name="mt5-server"
          autoComplete="off"
          required
          placeholder="e.g. Exness-MT5Trial9"
          value={form.server}
          onChange={(e) => setForm({ ...form, server: e.target.value })}
        />
      </div>
      <div className="flex flex-col gap-1 mb-3">
        <label className="text-[13px] text-text-muted">Account type</label>
        <select
          value={form.account_type}
          onChange={(e) => setForm({ ...form, account_type: e.target.value as "demo" | "live" })}
        >
          <option value="demo">demo</option>
          <option value="live">live</option>
        </select>
      </div>
      {error != null && <p className="text-negative">{String(error)}</p>}
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
    // Poll quickly while anything is actively being set up (this is a
    // first-time-setup moment the user is watching live), stop entirely
    // once nothing is in flight -- unlike Overview/Live's steady-state
    // refresh, a plain numeric interval here would poll forever even
    // for a user with zero in-flight jobs.
    refetchInterval: (query) => {
      const rows = query.state.data;
      const anyInFlight = rows?.some(
        (c) => c.provisioning_status === "pending" || c.provisioning_status === "in_progress",
      );
      return anyInFlight ? 5_000 : false;
    },
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

  const retryProvisioning = useMutation({
    mutationFn: (credentialId: string) => apiClient.post(`/broker-credentials/${credentialId}/retry-provisioning`),
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
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="m-0 text-2xl">Broker connection</h1>
      </div>

      {!credentialsQuery.isLoading && !hasAccounts && (
        // First-run: no accounts connected yet, so the connect form is
        // the whole point of this page rather than a buried nav item.
        <Card style={{ maxWidth: 480 }} className="mb-8">
          <h2 className="mt-0">Connect your MT5 account to get started</h2>
          <p className="text-text-muted">
            Saving your credentials starts automatic setup — we'll copy and configure an MT5 worker
            for this account. This usually takes a few minutes; you'll see live progress below, and
            the account will show up on the Live page once it's connected.
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
          <Card style={{ maxWidth: 480 }} className="mb-8">
            <h2 className="mt-0">Add another account</h2>
            <ConnectForm
              form={form}
              setForm={setForm}
              onSubmit={handleSubmit}
              submitting={createCredential.isPending}
              error={createCredential.error}
            />
          </Card>

          <h2 className="text-[13px] uppercase tracking-wide text-text-muted mb-3">Your accounts</h2>
          {credentialsQuery.error && <p className="text-negative">Failed to load: {String(credentialsQuery.error)}</p>}
          <Table>
            <thead>
              <tr>
                <th>Broker</th>
                <th>Account</th>
                <th>Server</th>
                <th>Type</th>
                <th>Active</th>
                <th>Status</th>
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
                  <td>
                    {(() => {
                      // Defensive fallback: an older backend deployment
                      // (pre-Phase-2) won't include provisioning_status
                      // in its response at all -- never assume a field
                      // added on the backend is already live everywhere
                      // this frontend might be pointed at.
                      const provisioningStatus = cred.provisioning_status ?? "not_requested";
                      return (
                        <>
                          <div className="flex items-center gap-2">
                            <Badge variant={provisioningBadgeVariant(provisioningStatus)}>
                              {provisioningStatus === "in_progress"
                                ? "provisioning"
                                : provisioningStatus.replace("_", " ")}
                            </Badge>
                            {provisioningStatus === "in_progress" && cred.provisioning_step && (
                              <span className="text-[13px] text-text-muted">
                                {provisioningStepLabel(cred.provisioning_step)}
                              </span>
                            )}
                          </div>
                          {provisioningStatus === "failed" && cred.provisioning_error && (
                            <p className="text-negative text-[13px] mt-1 mb-0">{cred.provisioning_error}</p>
                          )}
                        </>
                      );
                    })()}
                  </td>
                  <td>
                    <div className="flex gap-2">
                      <Button variant="secondary" disabled={mintToken.isPending} onClick={() => handleMintClick(cred)}>
                        {cred.bridge_configured ? "Re-mint bridge token" : "Mint bridge token"}
                      </Button>
                      {cred.provisioning_status === "failed" && (
                        <Button
                          variant="secondary"
                          disabled={retryProvisioning.isPending}
                          onClick={() => retryProvisioning.mutate(cred.credential_id)}
                        >
                          Retry
                        </Button>
                      )}
                    </div>
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
          message="Copy this now -- it won't be shown again."
          copyText={mintedToken}
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
