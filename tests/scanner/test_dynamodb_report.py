"""Tests for reporters/dynamodb_report.py — DynamoDB report writer."""

from decimal import Decimal

import pytest
from reporters.dynamodb_report import _batch_items, _float_to_decimal, _ttl_timestamp


class TestFloatToDecimal:
    def test_float_converted(self):
        result = _float_to_decimal(3.14)
        assert isinstance(result, Decimal)
        assert float(result) == pytest.approx(3.14)

    def test_nan_becomes_zero(self):
        result = _float_to_decimal(float("nan"))
        assert result == Decimal("0")

    def test_inf_becomes_zero(self):
        result = _float_to_decimal(float("inf"))
        assert result == Decimal("0")

    def test_nested_dict(self):
        result = _float_to_decimal({"a": 1.5, "b": "text"})
        assert result["a"] == Decimal("1.5")
        assert result["b"] == "text"

    def test_nested_list(self):
        result = _float_to_decimal([1.0, 2.5])
        assert result == [Decimal("1.0"), Decimal("2.5")]

    def test_non_float_passthrough(self):
        assert _float_to_decimal(42) == 42
        assert _float_to_decimal("hello") == "hello"
        assert _float_to_decimal(None) is None


class TestBatchItems:
    def test_exact_batches(self):
        items = list(range(10))
        batches = list(_batch_items(items, 5))
        assert len(batches) == 2
        assert batches[0] == [0, 1, 2, 3, 4]
        assert batches[1] == [5, 6, 7, 8, 9]

    def test_partial_last_batch(self):
        items = list(range(7))
        batches = list(_batch_items(items, 3))
        assert len(batches) == 3
        assert batches[2] == [6]

    def test_empty_list(self):
        assert list(_batch_items([], 5)) == []

    def test_single_item(self):
        batches = list(_batch_items(["a"], 25))
        assert len(batches) == 1
        assert batches[0] == ["a"]


class TestTtlTimestamp:
    def test_returns_future_epoch(self):
        import time

        now = int(time.time())
        ttl = _ttl_timestamp(90)
        assert ttl > now
        # Should be roughly 90 days from now (within 60s tolerance)
        expected = now + (90 * 86400)
        assert abs(ttl - expected) < 60
