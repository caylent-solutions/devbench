"""AuthVerifier preflight: mode-aware billing guard + non-root (AC-5/6/7).

Covers Section 3.6.1 / 3.6.2 / FR-20 / FR-21 / FR-23 / FR-25 under the two-mode
billing contract. Before creating any screen the supervisor must confirm:

SUBSCRIPTION mode (default):
  (a) no routing var that would push inference off-subscription is present in the
      operator env (FR-21) -- AWS workload creds are NOT a violation;
  (b) subscription auth (``claudeAiOauth.accessToken`` with ``user:inference``)
      is present (FR-20);
  (c) the process is non-root (Section 3.6.2).

BEDROCK mode:
  (a) no direct-Anthropic-API var is present (it would route to the direct API,
      not Bedrock);
  (b) NO subscription auth required; instead AWS creds + region prerequisites
      must be present (Bedrock billing prerequisites);
  (c) the process is still non-root.

Each failure is fail-fast with a clear message; ``claude`` must be resolvable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devbench.constants import (
    SUPERVISE_BILLING_MODE_BEDROCK,
    SUPERVISE_BILLING_MODE_SUBSCRIPTION,
)
from devbench.supervise import (
    AuthVerifier,
    SuperviseApiKeyPresentError,
    SuperviseAuthError,
    SuperviseBedrockPrereqError,
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


_BEDROCK_PREREQ_ENV = {
    "AWS_ACCESS_KEY_ID": "AKIA",
    "AWS_SECRET_ACCESS_KEY": "secret",
    "AWS_REGION": "us-east-1",
}


@pytest.mark.unit
class TestAuthVerifierSubscriptionApiKey:
    """AC-5: a routing var present in subscription mode fails fast (FR-21)."""

    def test_api_key_present_raises(self, tmp_path: Path) -> None:
        creds = _write_creds(tmp_path, scopes=["user:inference"])
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseApiKeyPresentError, match="ANTHROPIC_API_KEY"):
            verifier.verify(
                source_env={"ANTHROPIC_API_KEY": "sk-ant-xyz"},
                euid=1000,
                billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION,
            )

    def test_bedrock_var_present_raises_in_subscription(self, tmp_path: Path) -> None:
        creds = _write_creds(tmp_path, scopes=["user:inference"])
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseApiKeyPresentError, match="DEVBENCH_USE_BEDROCK"):
            verifier.verify(
                source_env={"DEVBENCH_USE_BEDROCK": "1"},
                euid=1000,
                billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION,
            )

    def test_aws_creds_present_is_not_a_billing_violation(self, tmp_path: Path) -> None:
        creds = _write_creds(tmp_path, scopes=["user:inference"])
        verifier = AuthVerifier(credentials_file=creds)
        verifier.verify(
            source_env={
                "PATH": "/usr/bin",
                "AWS_ACCESS_KEY_ID": "AKIA",
                "AWS_SECRET_ACCESS_KEY": "secret",
                "AWS_SESSION_TOKEN": "tok",
                "AWS_PROFILE": "default",
                "AWS_REGION": "us-east-1",
            },
            euid=1000,
            billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION,
        )


@pytest.mark.unit
class TestAuthVerifierSubscriptionAuth:
    """AC-6: subscription auth absent/invalid fails fast in subscription mode (FR-20)."""

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        verifier = AuthVerifier(credentials_file=tmp_path / "absent.json")
        with pytest.raises(SuperviseAuthError, match="subscription auth not found"):
            verifier.verify(source_env={}, euid=1000, billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION)

    def test_missing_inference_scope_raises(self, tmp_path: Path) -> None:
        creds = _write_creds(tmp_path, scopes=["user:profile"])
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseAuthError, match="user:inference"):
            verifier.verify(source_env={}, euid=1000, billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION)

    def test_empty_token_raises(self, tmp_path: Path) -> None:
        creds = _write_creds(tmp_path, scopes=["user:inference"], token="")
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseAuthError):
            verifier.verify(source_env={}, euid=1000, billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION)

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        creds = tmp_path / ".credentials.json"
        creds.write_text("{not json", encoding="utf-8")
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseAuthError):
            verifier.verify(source_env={}, euid=1000, billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION)

    def test_missing_oauth_object_raises(self, tmp_path: Path) -> None:
        creds = tmp_path / ".credentials.json"
        creds.write_text(json.dumps({"other": "data"}), encoding="utf-8")
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseAuthError, match="claudeAiOauth"):
            verifier.verify(source_env={}, euid=1000, billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION)

    def test_valid_subscription_passes(self, tmp_path: Path) -> None:
        creds = _write_creds(tmp_path, scopes=["user:inference", "user:profile"])
        verifier = AuthVerifier(credentials_file=creds)
        verifier.verify(
            source_env={"PATH": "/usr/bin"},
            euid=1000,
            billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION,
        )


@pytest.mark.unit
class TestAuthVerifierBedrockMode:
    """Bedrock mode: skip subscription auth; require AWS prereqs; still non-root."""

    def test_bedrock_does_not_require_subscription_auth(self, tmp_path: Path) -> None:
        verifier = AuthVerifier(credentials_file=tmp_path / "absent.json")
        verifier.verify(
            source_env={"PATH": "/usr/bin", **_BEDROCK_PREREQ_ENV},
            euid=1000,
            billing_mode=SUPERVISE_BILLING_MODE_BEDROCK,
        )

    def test_bedrock_missing_aws_creds_fails_fast(self, tmp_path: Path) -> None:
        verifier = AuthVerifier(credentials_file=tmp_path / "absent.json")
        with pytest.raises(SuperviseBedrockPrereqError, match="AWS"):
            verifier.verify(
                source_env={"PATH": "/usr/bin", "AWS_REGION": "us-east-1"},
                euid=1000,
                billing_mode=SUPERVISE_BILLING_MODE_BEDROCK,
            )

    def test_bedrock_missing_region_fails_fast(self, tmp_path: Path) -> None:
        verifier = AuthVerifier(credentials_file=tmp_path / "absent.json")
        with pytest.raises(SuperviseBedrockPrereqError, match="region"):
            verifier.verify(
                source_env={
                    "PATH": "/usr/bin",
                    "AWS_ACCESS_KEY_ID": "AKIA",
                    "AWS_SECRET_ACCESS_KEY": "secret",
                },
                euid=1000,
                billing_mode=SUPERVISE_BILLING_MODE_BEDROCK,
            )

    def test_bedrock_direct_anthropic_api_var_fails_fast(self, tmp_path: Path) -> None:
        verifier = AuthVerifier(credentials_file=tmp_path / "absent.json")
        with pytest.raises(SuperviseApiKeyPresentError, match="ANTHROPIC_API_KEY"):
            verifier.verify(
                source_env={"ANTHROPIC_API_KEY": "sk-ant", **_BEDROCK_PREREQ_ENV},
                euid=1000,
                billing_mode=SUPERVISE_BILLING_MODE_BEDROCK,
            )

    def test_bedrock_root_still_refused(self, tmp_path: Path) -> None:
        verifier = AuthVerifier(credentials_file=tmp_path / "absent.json")
        with pytest.raises(SuperviseRootError, match="root"):
            verifier.verify(
                source_env={"PATH": "/usr/bin", **_BEDROCK_PREREQ_ENV},
                euid=0,
                billing_mode=SUPERVISE_BILLING_MODE_BEDROCK,
            )


@pytest.mark.unit
class TestAuthVerifierRoot:
    """Section 3.6.2: refuse to launch as root (defense in depth)."""

    def test_root_raises(self, tmp_path: Path) -> None:
        creds = _write_creds(tmp_path, scopes=["user:inference"])
        verifier = AuthVerifier(credentials_file=creds)
        with pytest.raises(SuperviseRootError, match="root"):
            verifier.verify(source_env={}, euid=0, billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION)


@pytest.mark.unit
class TestRequireClaude:
    """FR-25/AC: ``claude`` must be resolvable on PATH else fail fast."""

    def test_screen_missing_message(self) -> None:
        with pytest.raises(FileNotFoundError, match="claude"):
            require_claude(which=lambda _name: None)

    def test_resolves_path(self) -> None:
        path = require_claude(which=lambda _name: "/usr/local/bin/claude")
        assert path == "/usr/local/bin/claude"
