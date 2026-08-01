from __future__ import annotations

import pytest

from app.emit_envelope import build_envelope


class TestBuildEnvelope:
    def test_target_id_is_chromadb_add(self):
        env = build_envelope({"documents": []}, domain="matematica")
        assert env["target_id"] == "tool.chromadb_add"

    def test_layers_match_runtime_to_tool(self):
        env = build_envelope({"documents": []}, domain="matematica")
        assert env["layer_from"] == "runtime"
        assert env["layer_to"] == "tool"

    def test_domain_goes_into_context(self):
        env = build_envelope({"documents": []}, domain="courier")
        assert env["context"]["domain"] == "courier"

    def test_payload_is_passed_through_unchanged(self):
        payload = {"documents": ["a", "b"], "ids": ["1", "2"], "metadatas": [{}, {}]}
        env = build_envelope(payload, domain="matematica")
        assert env["payload"] == payload

    def test_default_permission_level_is_execute_sandboxed(self):
        env = build_envelope({"documents": []}, domain="matematica")
        assert env["permissions"]["level"] == "execute_sandboxed"

    def test_custom_permission_level_is_honored(self):
        env = build_envelope({"documents": []}, domain="matematica", permission_level="full_access")
        assert env["permissions"]["level"] == "full_access"

    def test_invalid_domain_raises(self):
        with pytest.raises(ValueError, match="domain"):
            build_envelope({"documents": []}, domain="financeiro")

    def test_trace_id_auto_generated_when_not_provided(self):
        env = build_envelope({"documents": []}, domain="matematica")
        assert env["trace_id"]  # não vazio

    def test_explicit_trace_id_is_used(self):
        env = build_envelope({"documents": []}, domain="matematica", trace_id="trace-fixo-123")
        assert env["trace_id"] == "trace-fixo-123"

    def test_two_calls_without_explicit_trace_id_generate_different_ids(self):
        env1 = build_envelope({"documents": []}, domain="matematica")
        env2 = build_envelope({"documents": []}, domain="matematica")
        assert env1["trace_id"] != env2["trace_id"]
