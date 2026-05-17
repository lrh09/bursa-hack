// Shapes emitted by scripts/build_data.py → web/data/*.json
// Single source of truth. Imported by lib/data.ts and every page that
// renders strategy data.

export type GateStatus = "PASS" | "FAIL" | "PENDING";

export interface Gate {
  n: number;
  name: string;
  fullname: string;
  blurb: string | null;
  value: number | null;
  threshold: number | null;
  threshold_text: string | null;
  status: GateStatus;
}

export interface Diagnostic {
  name: string;
  blurb: string | null;
  value: number | null;
  threshold: number | null;
  status: GateStatus;
}

export interface KillTrigger {
  id: string;
  name: string;
  trigger: string;
  action: string;
}

export interface StrategyMetrics {
  wf_sharpe: number | null;
  wf_sharpe_std: number | null;
  oos_sharpe: number | null;
  cagr_oos: number | null;
  max_dd: number | null;
  pbo: number | null;
  dsr_eff: number | null;
  param_stab: number | null;
  cov: number | null;
  slip_drag: number | null;
  order_mult: number | null;
  monthly_hit: number | null;
  n_pass: number | null;
  n_eval: number | null;
  cost_bps_mean?: number | null;
  turnover_mean?: number | null;
  sharpe_min?: number | null;
  sharpe_max?: number | null;
  t_stat?: number | null;
  n_folds?: number | null;
}

export interface CalendarYearReturn {
  year: number;
  ret: number;
}

export interface MonthlyGridCell {
  year: number;
  month: number;
  ret: number;
}

export interface MonthlyGrid {
  years: number[];
  cells: MonthlyGridCell[];
}

export interface RollingSharpePoint {
  i: number;
  sharpe: number;
}

export interface HoldoutCapital {
  metrics: Record<string, number>;
  start_equity: number;
  final_equity: number;
}

export interface HoldoutVerdict {
  strategy: string;
  winner_params: Record<string, unknown>;
  n_trials_penalty: number;
  holdout_window: [string, string];
  results_by_capital: Record<string, HoldoutCapital>;
}

export interface Strategy {
  slug: string;
  label: string;
  family: string;
  rank: number;
  params: Record<string, unknown>;
  tier: string | null;
  recommendation: string | null;
  params_hash: string;
  narrative: string;
  metrics: StrategyMetrics;
  gates: Gate[];
  diagnostics: Diagnostic[];
  kill_triggers: KillTrigger[];
  equity_capitals: string[];
  calendar_year_returns?: CalendarYearReturn[];
  monthly_grid?: MonthlyGrid;
  rolling_sharpe?: RollingSharpePoint[];
  holdout_verdict?: HoldoutVerdict;
  trade_count?: number;
  has_trades?: boolean;
}

export interface EquityCurve {
  dates: string[];
  equity: number[];
  drawdown: number[];
  returns: number[];
}

export interface Trade {
  date: string;
  sec_id: string;
  qty: string;
  raw_px: string;
  adj_fill_px: string;
  notional_raw: string;
  fees: string;
  slippage: string;
  cost_bps: string;
  reason: string;
}

export interface VariantSummary {
  params_hash: string;
  strategy: string;
  params: Record<string, unknown>;
  capital: number;
  sharpe_mean: number;
  sharpe_std: number | null;
  sharpe_min: number | null;
  sharpe_max: number | null;
  cagr_mean: number;
  max_dd_mean: number;
  cost_bps_mean: number;
  turnover_mean: number;
  n_folds: number;
  t_stat: number | null;
  dsr: number | null;
  in_top_k: boolean;
  rank: number | null;
  tier: string | null;
  oos_sharpe: number | null;
}

export interface VariantDetail {
  params_hash: string;
  summary: VariantSummary;
  scorecard_row?: Record<string, string>;
  gates: Gate[];
}

export interface FoldRow {
  strategy: string;
  params_hash: string;
  fold_idx: number;
  train_start: string;
  validate_start: string;
  validate_end: string;
  capital: number;
  sharpe: number | null;
  cagr: number | null;
  max_drawdown: number | null;
  hit_rate: number | null;
  n_trades: number | null;
  skip_reason: string | null;
}

export interface ManifestStrategy {
  slug: string;
  label: string;
  family: string;
  tier: string | null;
  oos_sharpe: number | null;
}

export interface Manifest {
  generated_at: string;
  schema_version: number;
  strategies: ManifestStrategy[];
  variants_count: number;
  folds_count: number;
  search_log_rows: number;
  kill_triggers: KillTrigger[];
  iron: {
    holdout_window: [string, string];
    holdout_touch_count: number;
    no_live_trading: boolean;
    data_panel: [string, string];
    data_source: string;
  };
  families: string[];
}
