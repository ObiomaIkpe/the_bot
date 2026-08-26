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
