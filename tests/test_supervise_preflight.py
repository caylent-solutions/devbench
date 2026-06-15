"""AuthVerifier preflight: subscription auth + API-key guard + non-root (AC-5/6/7).

Covers Section 3.6.1 / 3.6.2 / FR-20 / FR-21 / FR-23 / FR-25: the supervisor must,
before creating any screen, confirm (a) no always-deny API-key var is present in
the operator env, (b) subscription auth (``claudeAiOauth.accessToken`` with the
``user:inference`` scope) is present, (c) the process is non-root, (d) ``claude``
is resolvable on PATH. Each failure is fail-fast with a clear message.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devbench.supervise import (
    AuthVerifier,
    SuperviseApiKeyPresentError,
    SuperviseAuthError,
    SuperviseRootError,
    require_claude,
)


def _write_creds(tmp_path: Path, *, scopes: list[str], token: str = "tok-123") -> Path:
    creds = tmp_path / ".credentials.json"
    creds.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": token, "scopes": scopes}}),
        encoding="utf-8",
    )
    return creds


@pytest.mark.unit
class TestAuthVerifierApiKey:
    """AC-5: an always-deny API-key var present in the env fails fast (FR-21)."""

    def test_api_key_present_raises(self, tmp_path: Path) -> None:
        creds = _write_creds(tmp_path, scopes=["user:inference"])
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseApiKeyPresentError, match="ANTHROPIC_API_KEY"):
            verifier.verify(source_env={"ANTHROPIC_API_KEY": "sk-ant-xyz"}, euid=1000)

    def test_bedrock_var_present_raises(self, tmp_path: Path) -> None:
        creds = _write_creds(tmp_path, scopes=["user:inference"])
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseApiKeyPresentError, match="DEVBENCH_USE_BEDROCK"):
            verifier.verify(source_env={"DEVBENCH_USE_BEDROCK": "1"}, euid=1000)


@pytest.mark.unit
class TestAuthVerifierSubscription:
    """AC-6: subscription auth absent/invalid fails fast (FR-20)."""

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        verifier = AuthVerifier(credentials_file=tmp_path / "absent.json")
        with pytest.raises(SuperviseAuthError, match="subscription auth not found"):
            verifier.verify(source_env={}, euid=1000)

    def test_missing_inference_scope_raises(self, tmp_path: Path) -> None:
        creds = _write_creds(tmp_path, scopes=["user:profile"])
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseAuthError, match="user:inference"):
            verifier.verify(source_env={}, euid=1000)

    def test_empty_token_raises(self, tmp_path: Path) -> None:
        creds = _write_creds(tmp_path, scopes=["user:inference"], token="")
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseAuthError):
            verifier.verify(source_env={}, euid=1000)

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        creds = tmp_path / ".credentials.json"
        creds.write_text("{not json", encoding="utf-8")
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseAuthError):
            verifier.verify(source_env={}, euid=1000)

    def test_missing_oauth_object_raises(self, tmp_path: Path) -> None:
        creds = tmp_path / ".credentials.json"
        creds.write_text(json.dumps({"other": "data"}), encoding="utf-8")
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseAuthError, match="claudeAiOauth"):
            verifier.verify(source_env={}, euid=1000)

    def test_valid_subscription_passes(self, tmp_path: Path) -> None:
        creds = _write_creds(tmp_path, scopes=["user:inference", "user:profile"])
        verifier = AuthVerifier(credentials_file=creds)
        # Non-root, valid creds, no api key: should not raise.
        verifier.verify(source_env={"PATH": "/usr/bin"}, euid=1000)


@pytest.mark.unit
class TestAuthVerifierRoot:
    """Section 3.6.2: refuse to launch as root (defense in depth)."""

    def test_root_raises(self, tmp_path: Path) -> None:
        creds = _write_creds(tmp_path, scopes=["user:inference"])
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseRootError, match="root"):
            verifier.verify(source_env={}, euid=0)


@pytest.mark.unit
class TestRequireClaude:
    """FR-25/AC: ``claude`` must be resolvable on PATH else fail fast."""

    def test_screen_missing_message(self) -> None:
        # AC-7 (screen) lives with the start preflight; here we assert claude.
        with pytest.raises(FileNotFoundError, match="claude"):
            require_claude(which=lambda _name: None)

    def test_resolves_path(self) -> None:
        path = require_claude(which=lambda _name: "/usr/local/bin/claude")
        assert path == "/usr/local/bin/claude"
