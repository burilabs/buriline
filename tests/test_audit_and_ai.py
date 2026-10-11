import json

from buriline import ai, audit, demo, analytics, inventory


def test_audit_chain_detects_tampering(tmp_path):
    trail = audit.AuditTrail(tmp_path / "a.jsonl")
    trail.append("frage", user="u", prompt="p", answer="a")
    trail.append("frage", user="u", prompt="p2", answer="a2")
    assert trail.verify()[0] is True
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    rec = json.loads(lines[0]); rec["answer"] = "manipuliert"
    lines[0] = json.dumps(rec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    (tmp_path / "a.jsonl").write_text("\n".join(lines) + "\n")
    ok, count, msg = trail.verify()
    assert ok is False and count == 1


def test_rule_based_explanations_use_computed_numbers():
    df = demo.generate_line_events(days=2)
    s = analytics.line_summary(df, demo.machine_rates())
    text = ai.RuleBasedProvider().explain_line(s)
    assert "Blister" in text and "Verfügbarkeit" in text
    top = s["pareto"][0]
    assert top["alarm_text"] in text


def test_inventory_summary_flags_problems():
    stock, mov = demo.generate_inventory()
    s = inventory.inventory_summary(stock, mov)
    assert s["totals"]["negative"] >= 1
    assert all(r["stock"] < 0 for r in s["negative"])
    assert all(r["reach_days"] is None or r["reach_days"] >= 0 for r in s["runout"])


def test_assistant_falls_back_without_ollama():
    a = ai.Assistant(ai.OllamaProvider(url="http://127.0.0.1:1"))
    df = demo.generate_line_events(days=1)
    res = a.ask("Was war der größte Stillstandsgrund?", {"line": analytics.line_summary(df, demo.machine_rates())})
    assert res.ok and res.provider == "regelbasiert" and "Stillstandsgrund" in res.text
