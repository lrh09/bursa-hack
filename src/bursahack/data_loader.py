"""Load price data for a date window, filter to equity universe, build a panel."""
from __future__ import annotations

import pandas as pd
import pyarrow.parquet as pq

from bursahack.engine import PricePanel, build_price_panel
from bursahack.paths import PARQUET_DIR
from bursahack.universe import equity_mask, TICKER_CHAINS


def load_panel(start_year: int, end_year: int, equity_only: bool = True) -> tuple[PricePanel, pd.DataFrame]:
    """Load parquet between [start_year, end_year], filter, build panel.

    Returns (panel, master) where master is a SECURITY_ID -> {ticker, name} table
    keeping only securities that appear in the panel.
    """
    df = pq.read_table(
        PARQUET_DIR,
        columns=[
            "SECURITY_ID", "TICKER", "NAME", "DATE",
            "OPEN", "CLOSE", "ADJ_OPEN", "ADJ_CLOSE", "ADJ_VOLUME",
        ],
        filters=[("year", ">=", start_year), ("year", "<=", end_year)],
    ).to_pandas()
    df["DATE"] = pd.to_datetime(df["DATE"])

    if equity_only:
        df = df[equity_mask(df["TICKER"])]

    # Vendor switched VOLUME from "thousand shares" to "actual shares" on 2014-06-03.
    # Pre-cutover days need x1000 to make units consistent with post-cutover.
    cutover = pd.Timestamp("2014-06-03")
    pre_mask = df["DATE"] < cutover
    df.loc[pre_mask, "ADJ_VOLUME"] = df.loc[pre_mask, "ADJ_VOLUME"] * 1000.0

    # Apply ticker chains (e.g. 5235SS -> 5089 lineage): we rewrite the SECURITY_ID
    # of the SUCCESSOR rows to the PREDECESSOR id so the engine sees one series.
    # Predecessor + successor cannot trade simultaneously by definition of a chain.
    chain_successor_to_pred = {}
    pred_to_succ = {v: k for k, v in TICKER_CHAINS.items()}
    # Resolve SECURITY_ID for each chain by ticker lookup
    tk_to_secid = (
        df.drop_duplicates("TICKER").set_index("TICKER")["SECURITY_ID"].to_dict()
    )
    for succ_ticker, pred_ticker in TICKER_CHAINS.items():
        succ_id = tk_to_secid.get(succ_ticker) or tk_to_secid.get(succ_ticker + ":MK")
        pred_id = tk_to_secid.get(pred_ticker) or tk_to_secid.get(pred_ticker + ":MK")
        if succ_id and pred_id:
            chain_successor_to_pred[succ_id] = pred_id
    if chain_successor_to_pred:
        df["SECURITY_ID"] = df["SECURITY_ID"].replace(chain_successor_to_pred)

    panel = build_price_panel(df)
    master = (
        df.drop_duplicates("SECURITY_ID")[["SECURITY_ID", "TICKER", "NAME"]]
        .set_index("SECURITY_ID")
        .sort_index()
    )
    return panel, master
