import pytest
from src.tracking.pricing import estimate_cost


def test_estimate_cost_gpt4o():
    # gpt-4o: input=$2.50/1M, output=$10.00/1M
    cost = estimate_cost("gpt-4o", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(12.50, rel=1e-4)


def test_estimate_cost_unknown_model():
    cost = estimate_cost("nonexistent-model-xyz", input_tokens=10000, output_tokens=5000)
    assert cost == 0.0
