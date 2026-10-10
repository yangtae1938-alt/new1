from pathlib import Path
import yaml

def load_config(path: str | Path = "config/radar.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

def hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)
