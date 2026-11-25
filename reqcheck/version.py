"""Версия агента для паспорта прогона: `git describe`, иначе версия пакета (FR-15)."""

import subprocess
from functools import lru_cache
from pathlib import Path

from reqcheck import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent


@lru_cache
def agent_version() -> str:
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        described = out.stdout.strip()
        if described:
            return described
    except (OSError, subprocess.SubprocessError):
        pass
    return f"v{__version__}"
