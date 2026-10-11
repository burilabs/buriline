"""Deterministic warehouse analytics on stock and movement exports."""
from __future__ import annotations

from typing import Any

import pandas as pd


def inventory_summary(stock: pd.DataFrame, movements: pd.DataFrame | None = None,
                      slow_days: int = 180) -> dict[str, Any]:
    s = stock.copy()
    if "last_movement" in s.columns:
        s["last_movement"] = pd.to_datetime(s["last_movement"], errors="coerce")
    if movements is not None and not movements.empty:
        # Derive average daily usage from the last 90 days of withdrawals when present.
        mv = movements.copy()
        mv["date"] = pd.to_datetime(mv["date"], errors="coerce")
        mv = mv.dropna(subset=["date"])
        horizon = (mv["date"].max() - mv["date"].min()).days + 1 if not mv.empty else 90
        horizon = max(horizon, 1)
        usage = (-mv[mv["qty"] < 0].groupby("sku")["qty"].sum() / horizon).rename("usage_from_movements")
        s = s.merge(usage, how="left", left_on="sku", right_index=True)
        s["avg_daily_usage"] = s["usage_from_movements"].fillna(s["avg_daily_usage"])
        last = mv.groupby("sku")["date"].max().rename("last_from_movements")
        s = s.merge(last, how="left", left_on="sku", right_index=True)
        if "last_movement" in s.columns:
            s["last_movement"] = s["last_from_movements"].fillna(s["last_movement"])
        else:
            s["last_movement"] = s["last_from_movements"]
    s["reach_days"] = (s["stock"] / s["avg_daily_usage"]).where((s["avg_daily_usage"] > 0) & (s["stock"] >= 0))
    s["stock_value"] = (s["stock"].clip(lower=0) * s["unit_cost"]).round(2)
    s["annual_usage_value"] = (s["avg_daily_usage"] * 365 * s["unit_cost"]).round(2)
    # ABC on annual usage value
    ranked = s.sort_values("annual_usage_value", ascending=False)
    total_val = ranked["annual_usage_value"].sum()
    cum = ranked["annual_usage_value"].cumsum() / total_val if total_val else 0
    ranked["abc"] = pd.cut(cum, bins=[-0.001, 0.8, 0.95, 1.0], labels=["A", "B", "C"]).astype(str)
    s = s.merge(ranked[["sku", "abc"]], on="sku", how="left")
    negative = s[s["stock"] < 0]
    short = s[(s["stock"] >= 0) & (s["stock"] < s["min_stock"])]
    runout = s[(s["reach_days"].notna()) & (s["reach_days"] < 14)].sort_values("reach_days")
    ref_date = pd.Timestamp(s["last_movement"].max()) if "last_movement" in s.columns and s["last_movement"].notna().any() else pd.Timestamp.today()
    if "last_movement" in s.columns:
        days_since = (ref_date - pd.to_datetime(s["last_movement"])).dt.days
        slow = s[(days_since > slow_days) & (s["stock"] > 0)].assign(days_since_movement=days_since)
    else:
        slow = s.iloc[0:0]
    cols = ["sku", "description", "stock", "min_stock", "avg_daily_usage", "reach_days", "unit_cost", "stock_value", "abc"]
    def recs(df: pd.DataFrame, extra: list[str] | None = None, n: int = 15) -> list[dict]:
        c = [x for x in cols + (extra or []) if x in df.columns]
        d = df[c].head(n).copy()
        if "reach_days" in d.columns:
            d["reach_days"] = d["reach_days"].round(1)
        if "last_movement" in d.columns:
            d["last_movement"] = d["last_movement"].astype(str)
        return d.to_dict(orient="records")
    return {
        "totals": {"skus": int(len(s)), "stock_value": round(float(s["stock_value"].sum()), 2),
                   "negative": int(len(negative)), "below_min": int(len(short)),
                   "runout_14d": int(len(runout)), "slow_movers": int(len(slow)),
                   "slow_value": round(float(slow["stock_value"].sum()), 2) if not slow.empty else 0.0},
        "abc": s.groupby("abc").agg(skus=("sku", "size"), value=("annual_usage_value", "sum")).round(2).reset_index().to_dict(orient="records"),
        "negative": recs(negative),
        "below_min": recs(short.sort_values("reach_days")),
        "runout": recs(runout),
        "slow_movers": recs(slow.sort_values("stock_value", ascending=False), ["days_since_movement"]),
        "reorder_suggestions": [
            {"sku": r["sku"], "description": r.get("description", ""), "stock": r["stock"],
             "reach_days": round(r["reach_days"], 1) if pd.notna(r["reach_days"]) else None,
             "suggested_qty": int(max(r["avg_daily_usage"] * 30 - max(r["stock"], 0), 0))}
            for r in runout.head(15).to_dict(orient="records")
        ],
    }
