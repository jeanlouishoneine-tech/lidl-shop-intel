"""Tests for api.py — error handling in fetch_offers and fetch_coupons."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import api


def test_fetch_offers_no_store_id(monkeypatch):
    monkeypatch.setenv("LIDL_STORE_ID", "")
    result = api.fetch_offers()
    assert result == []


def test_fetch_coupons_no_store_id(monkeypatch):
    monkeypatch.setenv("LIDL_STORE_ID", "")
    result = api.fetch_coupons()
    assert result == []


def test_fetch_offers_api_exception_returns_empty(monkeypatch):
    monkeypatch.setenv("LIDL_STORE_ID", "store_123")

    def _boom():
        raise Exception("network error")

    monkeypatch.setattr(api, "_client", _boom)
    result = api.fetch_offers()
    assert result == []


def test_fetch_offers_api_exception_logs_error(monkeypatch, caplog):
    monkeypatch.setenv("LIDL_STORE_ID", "store_123")

    def _boom():
        raise Exception("boom")

    monkeypatch.setattr(api, "_client", _boom)
    with caplog.at_level(logging.ERROR, logger="api"):
        api.fetch_offers()
    assert "fetch_offers failed" in caplog.text


def test_fetch_coupons_api_exception_returns_empty(monkeypatch):
    monkeypatch.setenv("LIDL_STORE_ID", "store_123")

    def _boom():
        raise Exception("network error")

    monkeypatch.setattr(api, "_client", _boom)
    result = api.fetch_coupons()
    assert result == []


def test_fetch_coupons_api_exception_logs_error(monkeypatch, caplog):
    monkeypatch.setenv("LIDL_STORE_ID", "store_123")

    def _boom():
        raise Exception("boom")

    monkeypatch.setattr(api, "_client", _boom)
    with caplog.at_level(logging.ERROR, logger="api"):
        api.fetch_coupons()
    assert "fetch_coupons failed" in caplog.text
