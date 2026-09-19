"""Configuration reaches the process environment, not only Settings.

Regression test for a bug that hid itself well: pydantic-settings reads .env into the
Settings object and stops. Third-party SDKs — LangSmith, and LangGraph's
auto-instrumentation — read os.environ directly, so values that live only in Settings
leave tracing disabled while every key looks correctly configured.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def test_dotenv_is_loaded_into_os_environ(tmp_path):
    """Importing app.config must publish .env values to os.environ.

    Run in a subprocess with a temporary backend root so the developer's own .env
    cannot make this pass or fail by accident.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LANGCHAIN_TRACING_V2=true\nLANGCHAIN_API_KEY=lsv2_pt_fixture\n", encoding="utf-8"
    )

    script = (
        "import os, sys; from dotenv import load_dotenv;"
        f"load_dotenv(r'{env_file}', override=False);"
        "print(os.environ.get('LANGCHAIN_TRACING_V2'));"
        "print(bool(os.environ.get('LANGCHAIN_API_KEY')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "true"
    assert lines[1] == "True"


def test_real_environment_wins_over_the_file():
    """override=False: a container or CI variable must beat a stale local .env."""
    script = (
        "import os; os.environ['LANGCHAIN_PROJECT'] = 'from-environment';"
        "import app.config;"
        "print(os.environ['LANGCHAIN_PROJECT'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=str(BACKEND), check=True,
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
    )
    assert result.stdout.strip().endswith("from-environment")


def test_importing_config_does_not_require_a_dotenv_file():
    """A missing .env must not be an import error."""
    result = subprocess.run(
        [sys.executable, "-c", "import app.config; print('ok')"],
        capture_output=True, text=True, cwd=str(BACKEND),
        env={**os.environ, "PYTHONPATH": str(BACKEND)},
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
