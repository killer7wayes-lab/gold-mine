"""Push docs/ (the dashboard and its JSON) to GitHub Pages with git."""
from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime

from . import config

log = logging.getLogger("goldmine.publish")
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _git(*args, timeout=60) -> tuple[int, str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")     # never hang on a password prompt
    try:
        res = subprocess.run(["git", *args], cwd=str(config.ROOT), env=env, timeout=timeout,
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             creationflags=_NO_WINDOW)
        return res.returncode, (res.stdout + res.stderr).strip()
    except FileNotFoundError:
        return 127, "git n'est pas installé"
    except subprocess.TimeoutExpired:
        return 124, "git ne répond pas (délai dépassé)"


def remote_url() -> str:
    if not (config.ROOT / ".git").exists():
        return ""
    code, out = _git("remote", "get-url", "origin")
    return out.strip() if code == 0 else ""


def pages_url() -> str:
    """https://github.com/user/repo(.git) -> https://user.github.io/repo/"""
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", remote_url())
    if not m:
        return ""
    user, repo = m.group(1), m.group(2)
    if repo.lower() == f"{user.lower()}.github.io":
        return f"https://{user}.github.io/"
    return f"https://{user}.github.io/{repo}/"


def set_remote(url: str) -> tuple[bool, str]:
    if not (config.ROOT / ".git").exists():
        code, out = _git("init", "-b", "main")
        if code:
            return False, out
    if remote_url():
        code, out = _git("remote", "set-url", "origin", url)
    else:
        code, out = _git("remote", "add", "origin", url)
    return code == 0, out


def publish() -> tuple[bool, str]:
    """Commit docs/ and push. Returns (ok, message in French)."""
    if not remote_url():
        return False, "pas de dépôt GitHub configuré (voir le guide d'installation)"
    _git("add", "docs")
    code, _ = _git("diff", "--cached", "--quiet")
    if code == 0:
        return True, "rien de nouveau à publier"
    ident = []
    if _git("config", "user.email")[0] != 0:
        ident = ["-c", "user.name=Gold Mine", "-c", "user.email=goldmine@localhost"]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    code, out = _git(*ident, "commit", "-m", f"Données du {stamp}", "--", "docs")
    if code:
        return False, f"commit impossible: {out[-200:]}"
    code, out = _git("push", "origin", "HEAD", timeout=180)
    if code:
        return False, f"envoi impossible (connexion GitHub ?): {out[-200:]}"
    return True, "tableau de bord publié"
