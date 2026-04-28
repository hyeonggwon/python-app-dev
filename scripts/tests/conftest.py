"""pytest config for harness self-tests.

These tests import scripts/orchestrate.py and scripts/run_gate.py as modules
to exercise pure functions (state machine transitions, gate enum mapping,
front-matter parsing, routing decisions) without spawning `claude` or shelling
out to real toolchains.

The harness has no `pyproject.toml` of its own — it's a meta-tool, not a
package — so we rely on conftest.py to make the scripts/ dir importable.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
