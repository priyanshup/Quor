from calc import add_prices


def test_add_prices_returns_int_sum() -> None:
    result = add_prices([100, 250, 375])
    assert result == 725
    assert isinstance(result, int)
