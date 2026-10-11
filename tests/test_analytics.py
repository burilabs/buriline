from datetime import datetime, timedelta

import pandas as pd

from buriline import analytics, ingest


def _events():
    t0 = datetime(2026, 9, 21, 6, 0)
    rows = [
        # machine M1: 3600 s RUN with 100 good + 0 reject at ideal 2/min -> performance 50/60=0.833
        (t0, "M1", "RUN", 3600, "", "", 100, 0),
        (t0 + timedelta(seconds=3600), "M1", "STOP", 600, "E1", "Störung A", 0, 0),
        (t0 + timedelta(seconds=4200), "M1", "STOP", 30, "E2", "Kurz", 0, 0),
        (t0 + timedelta(seconds=4230), "M1", "PLANNED_STOP", 1800, "", "Pause", 0, 0),
        (t0 + timedelta(seconds=6030), "M1", "RUN", 1200, "", "", 40, 10),
    ]
    df = pd.DataFrame(rows, columns=["timestamp", "machine", "state", "duration_s", "alarm_code", "alarm_text", "good_count", "reject_count"])
    df["batch"] = "B1"
    df["shift"] = "Früh"
    return df


def test_oee_numbers_are_exact():
    df = _events()
    oee = analytics.oee_per_machine(df, ideal_rates={"M1": 2.0})[0]
    planned = 3600 + 600 + 30 + 1200  # break excluded
    assert oee.planned_s == planned
    assert abs(oee.availability - 4800 / planned) < 1e-9
    total = 150
    assert oee.total_count == total
    expected_perf = (total / (4800 / 60)) / 2.0
    assert abs(oee.performance - expected_perf) < 1e-9
    assert abs(oee.quality - 140 / 150) < 1e-9
    assert abs(oee.oee - oee.availability * expected_perf * (140 / 150)) < 1e-9
    assert oee.stops == 2 and oee.microstops == 1
    assert abs(oee.mttr_min - (630 / 60 / 2)) < 1e-9


def test_pareto_and_microstops():
    df = _events()
    p = analytics.downtime_pareto(df)
    assert p.iloc[0]["alarm_code"] == "E1" and p.iloc[0]["duration_min"] == 10.0
    assert abs(p["share"].sum() - 1.0) < 1e-6
    m = analytics.microstops(df)
    assert len(m) == 1 and m.iloc[0]["alarm_code"] == "E2"


def test_ingest_maps_german_columns_and_states(tmp_path):
    csv = tmp_path / "export.csv"
    csv.write_text("Zeitstempel;Maschine;Zustand;Dauer;Fehlercode;Fehlertext;Gutstück;Ausschuss\n"
                   "2026-09-21 06:00:00;Blister;läuft;600;;;100;1\n"
                   "2026-09-21 06:10:00;Blister;Störung;120;E1;Folienriss;0;0\n", encoding="utf-8")
    df = ingest.load_line_events(csv)
    assert list(df["state"]) == ["RUN", "STOP"]
    assert df["good_count"].sum() == 100 and df["duration_s"].sum() == 720


def test_ingest_derives_duration_when_missing(tmp_path):
    csv = tmp_path / "e.csv"
    csv.write_text("timestamp,machine,state\n2026-09-21 06:00:00,M,RUN\n2026-09-21 06:05:00,M,STOP\n2026-09-21 06:06:00,M,RUN\n")
    df = ingest.load_line_events(csv)
    assert list(df["duration_s"]) == [300.0, 60.0, 0.0]


def test_ingest_rejects_missing_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    csv.write_text("a,b\n1,2\n")
    import pytest
    with pytest.raises(ingest.IngestError):
        ingest.load_line_events(csv)


def test_board_summary():
    board = pd.DataFrame({
        "date": ["2026-09-21"] * 3, "hour": [6, 7, 8], "shift": ["Früh"] * 3, "line": ["L1"] * 3,
        "order": ["A"] * 3, "target": [100, 100, 100], "actual": [100, 60, 90], "reason": ["", "Materialmangel", ""], "operator": ["x"] * 3,
    })
    s = analytics.board_summary(board)
    assert abs(s["totals"]["attainment"] - 250 / 300) < 1e-3
    assert s["totals"]["hours_below_target"] == 2
    assert s["reasons"][0]["reason"] == "Materialmangel" and s["reasons"][0]["lost"] == 40
    assert s["worst_hours"][0]["hour"] == 7
