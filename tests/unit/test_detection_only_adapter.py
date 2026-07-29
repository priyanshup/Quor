"""Tests for quor.adapters._detection_only.DetectionOnlyAdapter itself
(QB-069) — the base every detection-only adapter (Codex, Cursor, VS Code,
Windsurf, Aider, Continue.dev) is built on. Per-adapter behavior is covered
by each adapter's own test file plus the shared parametrized suite in
test_agent_adapter_protocol.py; this file covers only the base class's own
contract, in isolation, via a minimal concrete subclass.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from quor.adapters._detection_only import DetectionOnlyAdapter
from quor.adapters.base import AgentAdapter, DoctorContext


class _MinimalDetectionOnlyAdapter(DetectionOnlyAdapter):
    agent_id: ClassVar[str] = "minimal_test"
    display_name: ClassVar[str] = "Minimal Test Tool"
    limitation_reason: ClassVar[str] = "it is a test double with no real hook system."

    def _detect(self) -> tuple[bool, str]:
        return True, "always detected in this test double"


class _UndetectedAdapter(DetectionOnlyAdapter):
    agent_id: ClassVar[str] = "undetected_test"
    display_name: ClassVar[str] = "Undetected Test Tool"
    limitation_reason: ClassVar[str] = "it is a test double with no real hook system."

    def _detect(self) -> tuple[bool, str]:
        return False, "never detected in this test double"


class TestDetectionOnlyAdapterBaseContract:
    def test_satisfies_agent_adapter_protocol(self) -> None:
        assert isinstance(_MinimalDetectionOnlyAdapter(), AgentAdapter)

    def test_base_class_detect_is_not_implemented(self) -> None:
        # DetectionOnlyAdapter itself is not meant to be instantiated as a
        # real adapter — its _detect() is a deliberate NotImplementedError
        # so a subclass that forgets to override it fails loudly, not
        # silently, the first time doctor_checks() actually runs.
        with pytest.raises(NotImplementedError):
            DetectionOnlyAdapter()._detect()

    def test_doctor_checks_reflect_detect_result_regardless_of_outcome(self) -> None:
        detected_checks = _MinimalDetectionOnlyAdapter().doctor_checks(DoctorContext())
        undetected_checks = _UndetectedAdapter().doctor_checks(DoctorContext())

        detected_detail = next(d for n, _, d in detected_checks if "detected" in n)
        undetected_detail = next(d for n, _, d in undetected_checks if "detected" in n)

        assert "always detected" in detected_detail
        assert "never detected" in undetected_detail
        # Both are advisory (ok=True) regardless of detection outcome.
        assert all(ok is True for _, ok, _ in detected_checks)
        assert all(ok is True for _, ok, _ in undetected_checks)
