"""운영 모드 3분할(app/services/signal_mode.py): 명시 모드 우선·이전 호환 추정, 공격 플래그 연동, 사용자 설정 임계값 재판정."""
import pytest

from app.config import settings
from app.services import aggressive_mode, signal_mode as sm


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(settings, "QUANT_SIGNAL_MODE", "")
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MODE", False)
    monkeypatch.setattr(settings, "QUANT_CUSTOM_BUY_SCORE", 1)
    monkeypatch.setattr(settings, "QUANT_CUSTOM_SELL_SCORE", -1)
    monkeypatch.setattr(settings, "QUANT_CUSTOM_TREND_FILTER", False)


def test_legacy_resolution_from_flag_and_strategy(monkeypatch):
    assert sm.resolve("") == "custom"
    assert sm.resolve("ma_cross_kr") == "backtest"
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MODE", True)
    assert sm.resolve("ma_cross_kr") == "aggressive"
    assert aggressive_mode.is_enabled() is True


def test_explicit_mode_beats_env_flag(monkeypatch):
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MODE", True)      # fd compose 기본값
    monkeypatch.setattr(settings, "QUANT_SIGNAL_MODE", "backtest")
    assert sm.resolve("") == "backtest"
    assert aggressive_mode.is_enabled() is False
    monkeypatch.setattr(settings, "QUANT_SIGNAL_MODE", "custom")
    assert sm.resolve("ma_cross_kr") == "custom"
    monkeypatch.setattr(settings, "QUANT_SIGNAL_MODE", "aggressive")
    monkeypatch.setattr(settings, "QUANT_AGGRESSIVE_MODE", False)
    assert aggressive_mode.is_enabled() is True
    monkeypatch.setattr(settings, "QUANT_SIGNAL_MODE", "bogus")       # 알 수 없는 값은 미설정 취급
    assert sm.explicit() == "" and sm.resolve("") == "custom"


def test_custom_rules_rejudge_by_thresholds(monkeypatch):
    out = sm.apply_custom_rules({"action": "관망", "score": 1, "reasons": ["단기>중기"]})
    assert out["action"] == "매수" and out["custom_rules"] and "사용자 설정" in out["reasons"][-1]
    assert sm.apply_custom_rules({"score": 3})["action"] == "강력 매수"
    assert sm.apply_custom_rules({"score": -1})["action"] == "매도"
    assert sm.apply_custom_rules({"score": -3})["action"] == "강력 매도"
    monkeypatch.setattr(settings, "QUANT_CUSTOM_BUY_SCORE", 3)
    monkeypatch.setattr(settings, "QUANT_CUSTOM_SELL_SCORE", -3)
    assert sm.apply_custom_rules({"score": 2})["action"] == "관망"
    assert sm.apply_custom_rules({"score": -2})["action"] == "관망"


def test_custom_trend_filter_blocks_buy_below_ma20(monkeypatch):
    monkeypatch.setattr(settings, "QUANT_CUSTOM_TREND_FILTER", True)
    below = sm.apply_custom_rules({"score": 2}, {"current_price": 100.0, "ma20": [None, 105.0]})
    assert below["action"] == "관망" and "추세 필터" in below["reasons"][-1]
    above = sm.apply_custom_rules({"score": 2}, {"current_price": 110.0, "ma20": [None, 105.0]})
    assert above["action"] == "매수"
    nodata = sm.apply_custom_rules({"score": 2}, {"current_price": 110.0})
    assert nodata["action"] == "관망"
    # 매도는 필터와 무관
    assert sm.apply_custom_rules({"score": -2}, {"current_price": 100.0, "ma20": [105.0]})["action"] == "매도"
