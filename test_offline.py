"""
Tests for the parts of the system that don't need live Nebius/Neo4j
credentials — run these first, before ever touching real infra, to catch
logic bugs early.

Run with: python test_offline.py
"""
from graph_engine import _cosine
from llm_client import _strip_code_fence
from extraction import _fallback


def test_cosine_identical_vectors_is_one():
    v = [1.0, 2.0, 3.0]
    assert abs(_cosine(v, v) - 1.0) < 1e-9


def test_cosine_orthogonal_vectors_is_zero():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert abs(_cosine(a, b)) < 1e-9


def test_cosine_zero_vector_is_safe():
    a = [0.0, 0.0]
    b = [1.0, 1.0]
    assert _cosine(a, b) == 0.0


def test_strip_code_fence_removes_json_fence():
    raw = '```json\n{"a": 1}\n```'
    assert _strip_code_fence(raw) == '{"a": 1}'


def test_strip_code_fence_removes_plain_fence():
    raw = '```\n{"a": 1}\n```'
    assert _strip_code_fence(raw) == '{"a": 1}'


def test_strip_code_fence_passthrough_when_no_fence():
    raw = '{"a": 1}'
    assert _strip_code_fence(raw) == '{"a": 1}'


def test_extraction_fallback_has_required_keys():
    result = _fallback("some raw text")
    for key in ("summary", "importance", "event_time", "entities", "states", "actions", "relations"):
        assert key in result
    assert result["importance"] == 0.5
    assert result["entities"] == []


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    passed, failed = 0, 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {test.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        raise SystemExit(1)
