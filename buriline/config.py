"""Runtime configuration. Everything is overridable through environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("BURILINE_DATA_DIR", "./data")))
    audit_file: Path = field(default_factory=lambda: Path(_env("BURILINE_AUDIT_FILE", "./data/audit.jsonl")))
    ollama_url: str = field(default_factory=lambda: _env("BURILINE_OLLAMA_URL", "http://127.0.0.1:11434"))
    model: str = field(default_factory=lambda: _env("BURILINE_MODEL", "qwen2.5:3b"))
    language: str = field(default_factory=lambda: _env("BURILINE_LANG", "de"))
    microstop_seconds: int = field(default_factory=lambda: int(_env("BURILINE_MICROSTOP_SECONDS", "60")))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("BURILINE_LLM_TIMEOUT", "120")))
    # Machines are often blocked/starved by neighbours. These states count as downtime
    # but are reported separately so the bottleneck analysis can tell them apart.
    run_states: tuple[str, ...] = ("RUN",)
    planned_stop_states: tuple[str, ...] = ("PLANNED_STOP", "BREAK", "NO_PRODUCTION")
    flow_states: tuple[str, ...] = ("BLOCKED", "STARVED")

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
