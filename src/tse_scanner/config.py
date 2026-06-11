"""Configuration & parameters.

`Params` is the single source of truth for the strategy knobs and mirrors the
**DESIGN.md §10** starting-guess table 1:1. Every value here is a *starting guess* to be set by
the Phase-1 event study (DESIGN.md §9) — not a proven number. Keep this table and DESIGN.md §10
in sync; if they disagree, that's a bug.

Stdlib-only on purpose, so the CLI shell and tests import with zero third-party deps.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Params:
    # --- Universe filters (DESIGN.md §5) ---
    turnover_floor_yen: float = 100_000_000  # ≥ ¥100M/day, 20d avg — cut untradeable junk
    turnover_window: int = 20
    min_price_yen: float = 300               # ≥ ¥100–300
    require_taishaku: bool = True            # 貸借銘柄 only — thesis requirement (逆日歩/制度信用 trap)
    max_price_staleness_days: int = 10       # last bar within N calendar days of as-of — drops
                                             # delisted/halted names point-in-time (10y backtest)

    # --- Fuel: "are shorts crowded and trapped?" (DESIGN.md §3.1) ---
    days_to_cover_min: float = 5.0           # 信用売残 ÷ ADV — primary gauge of "trapped"
    credit_ratio_max: float = 1.0            # 信用倍率 (買残 ÷ 売残) gate: < 1.0 = short-heavy
    short_balance_trend_prints: int = 3      # 売残 rising across ~3 weekly prints = fuel building

    # --- Coil: "is the spring wound?" (DESIGN.md §3.2) ---
    bb_width_pctile_max: float = 0.20        # BB-width ≤ ~20th pct of baseline (TTM-squeeze convention)
    box_height_max_atr: float = 2.0          # tight box: height ≤ ~N×ATR  (TBD — DESIGN.md §10)

    # --- Ignition: "has it fired?" (DESIGN.md §3.3) ---
    volume_surge_mult: float = 2.0           # break-day volume ≥ ~2× 20d avg
    wide_bar_atr_mult: float = 1.5           # band-expanding bar: range ≥ ~1.5× ATR

    # --- Lookback windows (DESIGN.md §4) ---
    baseline_sessions: int = 120             # ~6mo reference for "compressed vs this name's normal"
    setup_sessions: int = 14                 # ~3 weeks ≈ ~3 weekly margin prints

    # --- Backtest success label: first-touch triple-barrier (DESIGN.md §6) ---
    target_pct: float = 0.12                 # +target hit first = win
    stop_pct: float = 0.06                   # −stop hit first = loss
    horizon_sessions: int = 10               # neither within horizon = scratch

    # --- Within-stage ranking (DESIGN.md §7) — fuel weighted highest; weights TBD by event study ---
    score_weights: dict[str, float] = field(
        default_factory=lambda: {"fuel": 0.60, "coil": 0.25, "ignition": 0.15}
    )


@dataclass(frozen=True)
class Settings:
    """Environment-derived paths & credentials (see .env.example).

    Every default is a `default_factory` so the environment is read at *instantiation*, after
    `load_settings()` has run python-dotenv — a plain `os.getenv(...)` default freezes at class
    definition (import) time and silently ignores `.env` (bit us on JQUANTS_API_KEY, 2026-06-11).
    """

    data_dir: Path = field(default_factory=lambda: Path(os.getenv("TSE_DATA_DIR", "./data")))
    duckdb_path: Path = field(
        default_factory=lambda: Path(os.getenv("TSE_DUCKDB_PATH", "./data/tse.duckdb")))
    # 'free' (JPX+Yahoo) | 'jquants' (paid history)
    data_source: str = field(default_factory=lambda: os.getenv("TSE_DATA_SOURCE", "free"))
    # J-Quants API V2 (2025-12-22+): single dashboard-issued key, sent as `x-api-key` header.
    jquants_api_key: str | None = field(
        default_factory=lambda: os.getenv("JQUANTS_API_KEY") or None)
    jsf_enabled: bool = field(
        default_factory=lambda: os.getenv("JSF_ENABLED", "false").lower() == "true")

    # --- Deep research (on-demand fundamental memo via claude CLI — DESIGN.md §15) ---
    research_model: str = field(
        default_factory=lambda: os.getenv("TSE_RESEARCH_MODEL", "claude-fable-5"))
    research_ttl_days: int = field(
        default_factory=lambda: int(os.getenv("TSE_RESEARCH_TTL_DAYS", "5")))
    research_timeout_s: int = field(
        default_factory=lambda: int(os.getenv("TSE_RESEARCH_TIMEOUT_S", "900")))
    research_max_turns: int = field(
        default_factory=lambda: int(os.getenv("TSE_RESEARCH_MAX_TURNS", "50")))
    claude_bin: str = field(default_factory=lambda: os.getenv("TSE_CLAUDE_BIN", "claude"))


def load_settings() -> Settings:
    """Load .env (if python-dotenv is installed) then read environment into Settings."""
    try:
        from dotenv import load_dotenv  # optional; absent in the stdlib-only smoke path

        load_dotenv()
    except ImportError:
        pass
    return Settings()


PARAMS = Params()
