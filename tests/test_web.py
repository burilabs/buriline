import os

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BURILINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BURILINE_AUDIT_FILE", str(tmp_path / "data" / "audit.jsonl"))
    monkeypatch.setenv("BURILINE_OLLAMA_URL", "http://127.0.0.1:1")
    import importlib
    from buriline import config
    importlib.reload(config)
    from buriline.web import app as webapp
    importlib.reload(webapp)
    from fastapi.testclient import TestClient
    return TestClient(webapp.app)


def test_empty_state_and_demo_flow(client):
    assert "Noch keine Daten" in client.get("/").text
    r = client.post("/daten/demo", follow_redirects=False)
    assert r.status_code == 303
    html = client.get("/").text
    assert "Stillstandsanalyse" in html and "Blister" in html
    assert "Stundentafel" in client.get("/stundentafel").text
    assert "Nachbestellvorschläge" in client.get("/lager").text
    j = client.post("/api/ask", data={"question": "Welche Maschine ist der Engpass?"}).json()
    assert j["ok"] and j["provider"] == "regelbasiert"
    audit = client.get("/api/status").json()["audit"]
    assert audit["ok"] is True and audit["records"] >= 2
    assert "Schichtbericht" in client.get("/bericht").text


def test_upload_rejects_bad_file(client):
    r = client.post("/daten/upload", data={"kind": "line_events"}, files={"file": ("x.csv", b"a,b\n1,2\n")}, follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]


def test_readonly_mode_blocks_upload_and_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("BURILINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BURILINE_AUDIT_FILE", str(tmp_path / "data" / "audit.jsonl"))
    monkeypatch.setenv("BURILINE_OLLAMA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("BURILINE_READONLY", "1")
    import importlib
    from buriline import config
    importlib.reload(config)
    from buriline.web import app as webapp
    importlib.reload(webapp)
    from fastapi.testclient import TestClient
    c = TestClient(webapp.app)
    r = c.post("/daten/demo", follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    r = c.post("/daten/upload", data={"kind": "line_events"}, files={"file": ("x.csv", b"a,b\n1,2\n")}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert "öffentliche Demo" in c.get("/").text
