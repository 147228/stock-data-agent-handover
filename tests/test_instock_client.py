import pytest

from stock_data_agent.instock_client import InStockClient, InStockConfig, _decode_oadates


def test_remote_url_is_blocked_by_default():
    with pytest.raises(ValueError):
        InStockClient(InStockConfig(base_url="https://example.com"))


def test_empty_allowlist_blocks_fetch():
    client = InStockClient(InStockConfig())
    with pytest.raises(PermissionError):
        client.fetch_module("anything")


def test_oadate_is_normalized():
    assert _decode_oadates("/OADate(2.0)/") == "1900-01-01"
