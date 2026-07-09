"""Unit tests for quor/pipeline/secrets.py: PA-F07 secret detection."""

from __future__ import annotations

from quor.pipeline.secrets import scan_for_secrets


class TestScanForSecrets:
    def test_no_secret_returns_empty(self) -> None:
        assert scan_for_secrets("nothing but ordinary git status output") == []

    def test_github_pat_detected(self) -> None:
        content = "auth failed: token ghp_" + "a" * 36 + " is invalid"
        found = scan_for_secrets(content)
        assert "GitHub token" in found

    def test_github_oauth_token_detected(self) -> None:
        content = "gho_" + "b" * 36
        assert "GitHub token" in scan_for_secrets(content)

    def test_aws_access_key_detected(self) -> None:
        content = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        assert "AWS access key ID" in scan_for_secrets(content)

    def test_slack_token_detected(self) -> None:
        content = "SLACK_TOKEN=xoxb-1234567890-abcdefghij"
        assert "Slack token" in scan_for_secrets(content)

    def test_private_key_header_detected(self) -> None:
        content = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----"
        assert "private key" in scan_for_secrets(content)

    def test_openssh_private_key_header_detected(self) -> None:
        content = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk...\n"
        assert "private key" in scan_for_secrets(content)

    def test_truncated_github_prefix_not_a_false_positive(self) -> None:
        """A short/truncated 'ghp_' fragment (not a real 36+ char token)
        must not match — this is exactly the false-positive risk the
        pattern's minimum length guards against."""
        assert scan_for_secrets("ghp_short") == []

    def test_multiple_secrets_all_reported(self) -> None:
        content = "ghp_" + "c" * 36 + "\nAKIAIOSFODNN7EXAMPLE"
        found = scan_for_secrets(content)
        assert "GitHub token" in found
        assert "AWS access key ID" in found
        assert len(found) == 2

    def test_does_not_modify_content(self) -> None:
        """scan_for_secrets is read-only — content passed in is untouched
        regardless of what it finds."""
        content = "ghp_" + "d" * 36
        scan_for_secrets(content)
        assert content == "ghp_" + "d" * 36
