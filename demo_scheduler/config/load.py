from __future__ import annotations

from pathlib import Path

import yaml

from demo_scheduler.config.schema import Config

DEFAULTS_PATH = Path(__file__).resolve().parents[2] / "configs" / "defaults.yaml"


def load_config(path: Path | None = None) -> Config:
    p = Path(path) if path else DEFAULTS_PATH
    with p.open() as f:
        raw = yaml.safe_load(f)
    return Config.model_validate(raw)
