"""S10: after sign-in, the console only ever navigates to a path on itself.

The callback page did ``router.replace(params.get("return_to"))`` with no
check, so ``/auth/callback?return_to=https://evil.example`` was an open
redirect on the customer's own domain. The backend's check also let
``/\\evil.example`` through, which browsers read as ``//evil.example``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from routes.auth import _is_safe_return_to

REPO = Path(__file__).resolve().parents[2]

SAFE = ["/bots", "/bots/hr/config?tab=limits", "/a/b#c"]
UNSAFE = ["", "https://evil.example", "//evil.example", "/\\evil.example", "/\\/evil.example",
          "javascript:alert(1)", "/\tevil", "/\x00x", "bots", "\\\\evil.example"]


@pytest.mark.parametrize("value", SAFE)
def test_backend_accepts_own_paths(value):
    assert _is_safe_return_to(value)


@pytest.mark.parametrize("value", UNSAFE)
def test_backend_refuses_other_origins(value):
    assert not _is_safe_return_to(value)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_console_rule_matches_backend(tmp_path):
    script = tmp_path / "check.mts"
    script.write_text(
        f'import {{ safeReturnTo }} from {json.dumps(str(REPO / "ui/lib/return-to.ts"))};\n'
        f"const values = {json.dumps(SAFE + UNSAFE)};\n"
        "console.log(JSON.stringify(values.map(v => safeReturnTo(v, \"FALLBACK\"))));\n"
    )
    out = subprocess.run(["node", "--no-warnings", str(script)], capture_output=True, text=True, check=True)
    results = json.loads(out.stdout)
    assert results[: len(SAFE)] == SAFE
    assert results[len(SAFE):] == ["FALLBACK"] * len(UNSAFE)


@pytest.mark.parametrize("page", ["ui/app/auth/callback/page.tsx", "ui/app/login/page.tsx"])
def test_pages_use_the_shared_rule(page):
    src = (REPO / page).read_text()
    assert 'safeReturnTo(params.get("return_to"))' in src
