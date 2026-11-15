"""Load / save the configuration.

config/config.json is written only by the control panel. It is merged over
defaults.DEFAULT_CONFIG so new default keys appear automatically.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from .defaults import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("GOLDMINE_DATA", ROOT / "data"))
DOCS_DIR = Path(os.environ.get("GOLDMINE_DOCS", ROOT / "docs"))
CONFIG_PATH = Path(os.environ.get("GOLDMINE_CONFIG", ROOT / "config" / "config.json"))
DB_PATH = DATA_DIR / "goldmine.db"
THUMBS_DIR = DATA_DIR / "thumbs"
LOG_DIR = DATA_DIR / "logs"


def deep_merge(base: dict, over: dict) -> dict:
    """Dicts merge key by key; anything else (lists, numbers) is replaced."""
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load() -> dict:
    user = {}
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            user = {}
    cfg = deep_merge(DEFAULT_CONFIG, user)
    # presets and keyword lists are user-owned once saved: never re-add defaults
    for key in ("presets",):
        if key in user:
            cfg[key] = copy.deepcopy(user[key])
    return cfg


def _changes(base, cfg):
    """Only what differs from the defaults, so improved defaults (new store
    selectors, new keys) keep reaching users who saved their settings."""
    if isinstance(base, dict) and isinstance(cfg, dict):
        out = {}
        for key, value in cfg.items():
            if key not in base:
                out[key] = value
            else:
                sub = _changes(base[key], value)
                if sub is not _SAME:
                    out[key] = sub
        return out if out else _SAME
    return _SAME if base == cfg else cfg


_SAME = object()


def save(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    changes = _changes(DEFAULT_CONFIG, cfg)
    if changes is _SAME:
        changes = {}
    if cfg.get("presets") != DEFAULT_CONFIG["presets"]:
        changes["presets"] = cfg["presets"]          # presets are replaced as a whole
    tmp.write_text(json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def active_countries(cfg: dict) -> list[str]:
    return [code for code, c in cfg["countries"].items() if c.get("active")]


def ensure_dirs() -> None:
    for d in (DATA_DIR, THUMBS_DIR, LOG_DIR, DOCS_DIR / "data" / "img"):
        d.mkdir(parents=True, exist_ok=True)


def scan_keywords(country_cfg: dict) -> list[str]:
    """What a scan really searches: the general keywords (unless the country is
    set to "niches only") plus the keywords of every ticked niche."""
    from .defaults import NICHES
    niche = [kw for name in country_cfg.get("niches") or [] for kw in NICHES.get(name, [])]
    general = [] if (country_cfg.get("niche_only") and niche) else country_cfg.get("keywords") or []
    return list(dict.fromkeys(k.strip() for k in [*general, *niche] if k and k.strip()))
