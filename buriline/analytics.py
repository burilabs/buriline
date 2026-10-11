"""Deterministic production analytics.

Every number shown in the UI or handed to the language model is produced here with
plain pandas arithmetic, so it can be reproduced by hand from the source export.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import pandas as pd

from .config import settings

DOWNTIME_STATES = ("STOP", "SETUP", "BLOCKED", "STARVED", "IDLE")


@dataclass
class OEE:
    machine: str
    planned_s: float
    run_s: float
    stop_s: float
    setup_s: float
    flow_loss_s: float   # blocked + starved
    total_count: int
    good_count: int
    reject_count: int
    ideal_rate_ppm: float | None
    availability: float
    performance: float | None
    quality: float
    oee: float | None
    stops: int
    microstops: int
    mtbf_min: float | None
    mttr_min: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rate_from_data(df: pd.DataFrame) -> float | None:
    """Estimate the ideal rate as the 95th percentile of observed run rates (units/min)."""
    runs = df[(df["state"] == "RUN") & (df["duration_s"] > 0)]
    if runs.empty:
        return None
    rates = (runs["good_count"] + runs["reject_count"]) / (runs["duration_s"] / 60.0)
    rates = rates[rates > 0]
    if rates.empty:
        return None
    return float(rates.quantile(0.95))


def oee_per_machine(df: pd.DataFrame, ideal_rates: dict[str, float] | None = None,
                    microstop_seconds: int | None = None) -> list[OEE]:
    ms = microstop_seconds or settings.microstop_seconds
    ideal_rates = ideal_rates or {}
    out: list[OEE] = []
    for machine, g in df.groupby("machine", sort=True):
        planned = g[~g["state"].isin(settings.planned_stop_states)]
        planned_s = float(planned["duration_s"].sum())
        run_s = float(g.loc[g["state"] == "RUN", "duration_s"].sum())
        stop_rows = g[g["state"] == "STOP"]
        stop_s = float(stop_rows["duration_s"].sum())
        setup_s = float(g.loc[g["state"] == "SETUP", "duration_s"].sum())
        flow_s = float(g.loc[g["state"].isin(settings.flow_states), "duration_s"].sum())
        good = int(g["good_count"].sum())
        reject = int(g["reject_count"].sum())
        total = good + reject
        rate = ideal_rates.get(machine) or _rate_from_data(g)
        availability = run_s / planned_s if planned_s else 0.0
        performance = None
        if rate and run_s:
            performance = min(1.0, (total / (run_s / 60.0)) / rate)
        quality = good / total if total else 1.0
        oee = availability * performance * quality if performance is not None else None
        stops = int(len(stop_rows))
        micro = int((stop_rows["duration_s"] < ms).sum())
        out.append(OEE(
            machine=str(machine), planned_s=planned_s, run_s=run_s, stop_s=stop_s, setup_s=setup_s,
            flow_loss_s=flow_s, total_count=total, good_count=good, reject_count=reject,
            ideal_rate_ppm=rate, availability=availability, performance=performance, quality=quality,
            oee=oee, stops=stops, microstops=micro,
            mtbf_min=(run_s / 60.0 / stops) if stops else None,
            mttr_min=(stop_s / 60.0 / stops) if stops else None,
        ))
    return out


def downtime_pareto(df: pd.DataFrame, machine: str | None = None, top: int = 10) -> pd.DataFrame:
    g = df[df["state"] == "STOP"]
    if machine:
        g = g[g["machine"] == machine]
    if g.empty:
        return pd.DataFrame(columns=["machine", "alarm_code", "alarm_text", "count", "duration_min", "share"])
    key = ["machine", "alarm_code", "alarm_text"]
    agg = g.groupby(key, dropna=False).agg(count=("duration_s", "size"), duration_s=("duration_s", "sum")).reset_index()
    agg["duration_min"] = (agg["duration_s"] / 60.0).round(1)
    total = agg["duration_s"].sum()
    agg["share"] = (agg["duration_s"] / total).round(4) if total else 0.0
    agg = agg.sort_values("duration_s", ascending=False).head(top)
    return agg.drop(columns=["duration_s"]).reset_index(drop=True)


def microstops(df: pd.DataFrame, microstop_seconds: int | None = None) -> pd.DataFrame:
    ms = microstop_seconds or settings.microstop_seconds
    g = df[(df["state"] == "STOP") & (df["duration_s"] < ms)]
    if g.empty:
        return pd.DataFrame(columns=["machine", "alarm_code", "alarm_text", "count", "duration_min"])
    agg = g.groupby(["machine", "alarm_code", "alarm_text"]).agg(count=("duration_s", "size"), duration_s=("duration_s", "sum")).reset_index()
    agg["duration_min"] = (agg["duration_s"] / 60.0).round(1)
    return agg.sort_values("count", ascending=False).drop(columns=["duration_s"]).reset_index(drop=True)


def per_shift(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (day, shift), g in df.groupby([df["timestamp"].dt.date, "shift"]):
        planned = g[~g["state"].isin(settings.planned_stop_states)]["duration_s"].sum()
        if planned < 1800:
            continue  # partial shift at the edge of the export, not meaningful
        run = g.loc[g["state"] == "RUN", "duration_s"].sum()
        rows.append({
            "date": str(day), "shift": shift,
            "availability": round(run / planned, 4) if planned else 0.0,
            "good_count": int(g["good_count"].sum()), "reject_count": int(g["reject_count"].sum()),
            "stop_min": round(g.loc[g["state"] == "STOP", "duration_s"].sum() / 60.0, 1),
            "stops": int((g["state"] == "STOP").sum()),
        })
    order = {"Früh": 0, "Spät": 1, "Nacht": 2}
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["_o"] = out["shift"].map(lambda s: order.get(s, 9))
    return out.sort_values(["date", "_o"]).drop(columns="_o").reset_index(drop=True)


def bottleneck(oees: list[OEE]) -> dict[str, Any] | None:
    """The machine with the largest own downtime (stops + setup), ignoring blocked/starved time
    which is caused by neighbours. Also reports who waits for whom."""
    if not oees:
        return None
    own = {o.machine: o.stop_s + o.setup_s for o in oees}
    worst = max(own, key=own.get)
    waiting = {o.machine: o.flow_loss_s for o in oees}
    return {
        "machine": worst,
        "own_downtime_min": round(own[worst] / 60.0, 1),
        "flow_loss_min_by_machine": {m: round(v / 60.0, 1) for m, v in waiting.items()},
        "lowest_availability": min(oees, key=lambda o: o.availability).machine,
    }


def daily_trend(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for day, g in df.groupby(df["timestamp"].dt.date):
        planned = g[~g["state"].isin(settings.planned_stop_states)]["duration_s"].sum()
        run = g.loc[g["state"] == "RUN", "duration_s"].sum()
        rows.append({"date": str(day), "availability": round(run / planned, 4) if planned else 0.0,
                     "good_count": int(g["good_count"].sum()),
                     "stop_min": round(g.loc[g["state"] == "STOP", "duration_s"].sum() / 60.0, 1)})
    return pd.DataFrame(rows)


def line_summary(df: pd.DataFrame, ideal_rates: dict[str, float] | None = None) -> dict[str, Any]:
    """Everything the dashboard and the language model need, as plain JSON-able data."""
    oees = oee_per_machine(df, ideal_rates)
    pareto = downtime_pareto(df)
    micro = microstops(df)
    shifts = per_shift(df)
    trend = daily_trend(df)
    start, end = df["timestamp"].min(), df["timestamp"].max()
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat(), "days": int((end - start).days) + 1},
        "machines": [o.as_dict() for o in oees],
        "line_oee": _line_oee(oees),
        "pareto": pareto.to_dict(orient="records"),
        "microstops": micro.head(10).to_dict(orient="records"),
        "microstop_threshold_s": settings.microstop_seconds,
        "shifts": shifts.to_dict(orient="records"),
        "trend": trend.to_dict(orient="records"),
        "bottleneck": bottleneck(oees),
        "totals": {
            "good_count": int(df["good_count"].sum()), "reject_count": int(df["reject_count"].sum()),
            "stop_min": round(df.loc[df["state"] == "STOP", "duration_s"].sum() / 60.0, 1),
            "stops": int((df["state"] == "STOP").sum()),
        },
    }


def _line_oee(oees: list[OEE]) -> dict[str, Any]:
    """Line OEE is driven by the last machine (what leaves the line) and the lowest availability."""
    if not oees:
        return {}
    last = oees[-1]
    return {"availability_min": round(min(o.availability for o in oees), 4),
            "oee_last_machine": round(last.oee, 4) if last.oee is not None else None,
            "machine": last.machine}


# ---------------------------------------------------------------- hourly board

def board_summary(board: pd.DataFrame) -> dict[str, Any]:
    b = board.copy()
    b["deviation"] = b["actual"] - b["target"]
    b["attainment"] = (b["actual"] / b["target"]).where(b["target"] > 0, 0.0)
    by_shift = b.groupby(["date", "shift"]).agg(target=("target", "sum"), actual=("actual", "sum")).reset_index()
    by_shift["attainment"] = (by_shift["actual"] / by_shift["target"]).round(4)
    reasons = b[b["reason"] != ""].groupby("reason").agg(hours=("reason", "size"), lost=("deviation", "sum")).reset_index()
    reasons["lost"] = -reasons["lost"]
    reasons = reasons.sort_values("lost", ascending=False)
    worst_hours = b.sort_values("deviation").head(10)
    by_weekday_shift = b.copy()
    by_weekday_shift["weekday"] = pd.to_datetime(by_weekday_shift["date"]).dt.day_name()
    wds = by_weekday_shift.groupby(["weekday", "shift"]).agg(attainment=("attainment", "mean")).reset_index()
    wds["attainment"] = wds["attainment"].round(4)
    return {
        "period": {"from": str(b["date"].min()), "to": str(b["date"].max())},
        "totals": {"target": int(b["target"].sum()), "actual": int(b["actual"].sum()),
                   "attainment": round(float(b["actual"].sum() / b["target"].sum()), 4) if b["target"].sum() else 0.0,
                   "hours": int(len(b)), "hours_below_target": int((b["deviation"] < 0).sum())},
        "by_shift": by_shift.to_dict(orient="records"),
        "reasons": reasons.to_dict(orient="records"),
        "worst_hours": worst_hours[["date", "hour", "shift", "order", "target", "actual", "reason"]].to_dict(orient="records"),
        "weekday_shift": wds.to_dict(orient="records"),
        "rows": b.to_dict(orient="records"),
    }
