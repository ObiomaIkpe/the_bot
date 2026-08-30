// Mirrors the backend's Pydantic response schemas (app/schemas/*.py) --
// keep these in sync by hand when those change, same as this frontend
// keeps its own copy of anything else the backend defines the shape of.

export interface EventOut {
  event_id: string;
  model: string;
  event_type: string;
  timestamp: string;
  details: Record<string, unknown>;
  is_shadow: boolean;
}

export interface TradeOut {
  trade_id: string;
  model: string;
  is_shadow: boolean;
  direction: string;
  entry_price: number;
  stop_price: number;
  target_price: number;
  exit_price: number | null;
  outcome: string | null;
  realized_r: number | null;
  entry_time_utc: string;
  entry_time_ny: string;
  exit_time_utc: string | null;
  real_status: string | null;
  real_fill_price: number | null;
  real_close_price: number | null;
  real_close_reason: string | null;
  real_profit: number | null;
}

export type ModelStatus = "disabled" | "shadow" | "active";

export interface ModelConfigOut {
  config_id: string;
  model_name: string;
  status: ModelStatus;
  risk_pct: number;
  magic_number: number;
  max_concurrent_positions: number | null;
  is_paused: boolean;
}

export interface UserSettingsOut {
  setting_id: string;
  instrument: string;
  max_daily_loss_pct: number;
  news_filters: Record<string, unknown>;
  demo_or_live: string;
  is_paused: boolean;
}

export interface Position {
  ticket: number;
  symbol: string;
  direction: string;
  volume: number;
  open_price: number;
  current_price: number;
  stop_loss: number;
  take_profit: number;
  profit: number;
  magic: number;
  time_utc: string;
  time_ny: string;
}

export interface PendingOrder {
  order_ticket: number;
  symbol: string;
  direction: string;
  volume: number;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  magic: number;
  time_utc: string;
  time_ny: string;
}

export type ProvisioningStatus =
  | "not_requested"
  | "pending"
  | "in_progress"
  | "active"
  | "failed"
  | "decommissioning"
  | "removing"
  | "removed"
  | "decommission_failed";

export interface BrokerCredentialOut {
  credential_id: string;
  broker_name: string;
  account_login: string;
  server: string;
  account_type: "demo" | "live";
  is_active: boolean;
  bridge_configured: boolean;
  provisioning_status: ProvisioningStatus;
  provisioning_step: string | null;
  provisioning_error: string | null;
}

export interface BrokerCredentialCreate {
  broker_name: string;
  account_login: string;
  account_password: string;
  server: string;
  account_type: "demo" | "live";
}

export interface AccountInfo {
  login: number;
  server: string;
  balance: number;
  equity: number;
  margin: number;
  margin_free: number;
  margin_level: number | null;
  leverage: number;
  currency: string;
}

export interface BridgeHealth {
  status: string;
  account_label: string;
  login: number;
  connected: boolean;
  trade_allowed: boolean | null;
  detail: string | null;
}

export interface CurrentUser {
  user_id: string;
  email: string;
  is_active: boolean;
  is_admin: boolean;
}

// ---------- Admin (app/routers/admin.py) ----------
// Each Admin*Out is its single-user equivalent above plus user_email --
// admin_dashboard/ (the Streamlit tool these replace) never showed
// which user a row belonged to at all, so this is a genuine addition,
// not just a straight port.

export type AdminEventOut = EventOut & { user_email: string };
export type AdminTradeOut = TradeOut & { user_email: string };
export type AdminModelConfigOut = ModelConfigOut & { user_email: string };

export interface AdminEventChainOut {
  day_events: AdminEventOut[];
  matched_fill_event_id: string | null;
  matched_close_event_id: string | null;
}

export type AuditLogActorType = "user" | "machine" | "credential" | "unknown";

export interface AuditLogOut {
  audit_id: string;
  timestamp: string;
  actor_type: AuditLogActorType;
  actor_id: string | null;
  actor_label: string | null;
  event_type: string;
  resource_type: string | null;
  resource_id: string | null;
  details: Record<string, unknown>;
  ip_address: string | null;
}
