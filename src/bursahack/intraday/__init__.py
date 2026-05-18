"""Intraday research platform — scalable from 1y/1m sample to 10y/multi-exchange.

Design choice: storage is hive-partitioned parquet keyed on (exchange, year, month)
with `code` kept as a column, NEVER as a partition. This is the documented #1
anti-pattern for DuckDB/Polars when symbol cardinality is in the thousands.
Query layer uses Polars (lazy DataFrame ops) + DuckDB (ASOF joins, SQL shapes).

Modules:
- calendar : ExchangeCalendar facade (XKLS-aware, KL <-> UTC, phantom-bar mask).
- loader   : load_bars(start, end, universe, freq) lazy Polars; snapshot_hash.
- universe : Liquidity-screened, as-of-date, monthly-rebalanced membership.
- impact   : Kissell-Glantz market impact + Rogers-Satchell sigma.
- config   : Pydantic + YAML layered config (_extends inheritance).
"""
from __future__ import annotations
