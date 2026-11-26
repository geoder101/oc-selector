"""Root conftest: make the repo root importable under ``uv run pytest``.

``just test`` runs the ``pytest`` console script (not ``python -m pytest``),
which does not add the repo root to ``sys.path``; insert it so ``import tools``
resolves.

Supporting change for the classifier tests; does not touch pyproject.toml,
justfile or tools/*.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
