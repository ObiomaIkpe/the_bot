import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiClient, ApiError } from "../api/client";
import type { CurrentUser } from "../api/types";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card } from "../components/Card";

export function Profile() {
  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: () => apiClient.get<CurrentUser>("/auth/me"),
  });

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const changePassword = useMutation({
    mutationFn: () => apiClient.post("/auth/change-password", { current_password: currentPassword, new_password: newPassword }),
    onSuccess: () => {
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setSuccess(true);
    },
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSuccess(false);
    setConfirmError(null);
    if (newPassword !== confirmPassword) {
      setConfirmError("New password and confirmation don't match.");
      return;
    }
    changePassword.mutate();
  }

  return (
    <div>
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="m-0 text-2xl">Profile</h1>
      </div>

      {meQuery.isLoading && <p>Loading...</p>}
      {meQuery.error && <p className="text-negative">Failed to load profile: {String(meQuery.error)}</p>}
      {meQuery.data && (
        <Card style={{ maxWidth: 420 }} className="mb-8">
          <div className="flex items-center justify-between mb-2">
            <span className="text-text-muted text-[13px]">Email</span>
            <Badge variant={meQuery.data.is_active ? "active" : "disabled"}>
              {meQuery.data.is_active ? "active" : "inactive"}
            </Badge>
          </div>
          <div className="text-lg">{meQuery.data.email}</div>
        </Card>
      )}

      <h2 className="text-[13px] uppercase tracking-wide text-text-muted mb-3">Change password</h2>
      <Card style={{ maxWidth: 420 }}>
        <form onSubmit={handleSubmit}>
          <div className="flex flex-col gap-1 mb-3">
            <label className="text-[13px] text-text-muted">Current password</label>
            <input
              type="password"
              required
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1 mb-3">
            <label className="text-[13px] text-text-muted">New password</label>
            <input
              type="password"
              required
              minLength={8}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1 mb-3">
            <label className="text-[13px] text-text-muted">Confirm new password</label>
            <input
              type="password"
              required
              minLength={8}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
            />
          </div>
          {confirmError && <p className="text-negative">{confirmError}</p>}
          {changePassword.error && (
            <p className="text-negative">
              {changePassword.error instanceof ApiError ? changePassword.error.detail : "Failed to change password"}
            </p>
          )}
          {success && <p className="text-positive">Password updated.</p>}
          <Button type="submit" variant="primary" disabled={changePassword.isPending}>
            {changePassword.isPending ? "Updating..." : "Update password"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
