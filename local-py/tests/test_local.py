"""로컬 자동매매 자가 점검 — 표준 unittest. `python3 -m unittest discover tests` 로 실행한다.

네트워크를 쓰지 않는다(mock 시세 공급자 + 임시 상태 파일).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lumina_local import aggressive, risk_guard, strategy                 # noqa: E402
from lumina_local.auto_trade import AutoTrader                            # noqa: E402
from lumina_local.backtest import run as run_backtest                     # noqa: E402
from lumina_local.config import Settings                                  # noqa: E402
from lumina_local.indicators import calc_rsi, calc_sma, quant_indicators  # noqa: E402
from lumina_local.market_data import MarketData, mock_candles             # noqa: E402
from lumina_local.store import Store                                      # noqa: E402


def _settings(tmp: Path, **kw) -> Settings:
    base = Settings(data_dir=tmp, market_data_source="mock", mode="paper",
                    risk_cooldown_min=0, ai_top_n=3)
    return replace(base, **kw).clamped()


class IndicatorTests(unittest.TestCase):
    def test_sma_and_rsi_windows(self):
        closes = [float(i) for i in range(1, 31)]
        self.assertIsNone(calc_sma(closes, 5)[3])
        self.assertEqual(calc_sma(closes, 5)[4], 3.0)
        # 하락이 없으면 원본 구현이 rs 를 100 으로 고정하므로 RSI 는 99.01 이 된다(app 과 동일)
        self.assertEqual(calc_rsi(closes)[-1], 99.01)

    def test_signal_needs_20_bars(self):
        self.assertEqual(quant_indicators("X", mock_candles("005930.KS", "1y", "1d")["candles"][:10])["error"],
                         "데이터 부족")

    def test_signal_shape(self):
        ind = quant_indicators("005930.KS", mock_candles("005930.KS", "1y", "1d")["candles"])
        self.assertIn(ind["signal"]["action"], ("강력 매수", "매수", "관망", "매도", "강력 매도"))
        self.assertTrue(ind["current_price"] > 0)
        self.assertEqual(len(ind["closes"]), 100)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state.json", "local", 1_000_000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_buy_then_sell_updates_cash_and_avg(self):
        self.store.execute_trade("005930.KS", "삼성전자", "buy", 10_000, 10, "테스트")
        self.store.execute_trade("005930.KS", "삼성전자", "buy", 20_000, 10, "테스트")
        pos = self.store.position("005930.KS")
        self.assertEqual(pos["quantity"], 20)
        self.assertEqual(pos["avg_price"], 15_000)
        self.assertEqual(self.store.cash, 1_000_000 - 300_000)
        trade = self.store.execute_trade("005930.KS", "삼성전자", "sell", 20_000, 20, "테스트")
        self.assertEqual(trade["status"], "filled")
        self.assertIsNone(self.store.position("005930.KS"))
        self.assertEqual(self.store.cash, 1_100_000)

    def test_buy_shrinks_to_affordable_quantity(self):
        trade = self.store.execute_trade("005930.KS", "삼성전자", "buy", 400_000, 10, "테스트")
        self.assertEqual(trade["status"], "filled")
        self.assertEqual(trade["quantity"], 2)          # 100만원으로 40만원짜리 2주

    def test_buy_skipped_when_cash_too_small(self):
        trade = self.store.execute_trade("005930.KS", "삼성전자", "buy", 2_000_000, 1, "테스트")
        self.assertEqual(trade["status"], "skipped")
        self.assertEqual(self.store.cash, 1_000_000)

    def test_sell_without_position_is_skipped(self):
        trade = self.store.execute_trade("005930.KS", "삼성전자", "sell", 10_000, 1, "테스트")
        self.assertEqual(trade["status"], "skipped")

    def test_state_survives_reload(self):
        self.store.execute_trade("005930.KS", "삼성전자", "buy", 10_000, 3, "테스트")
        again = Store(self.store.path, "local", 1_000_000)
        self.assertEqual(again.position("005930.KS")["quantity"], 3)


class RiskGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state.json", "local", 1_000_000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cooldown_blocks_second_order(self):
        self.assertTrue(risk_guard.acquire_order_slot(self.store, "005930.KS", "buy", 30))
        self.assertFalse(risk_guard.acquire_order_slot(self.store, "005930.KS", "buy", 30))
        self.assertTrue(risk_guard.acquire_order_slot(self.store, "005930.KS", "sell", 30))   # 방향은 별도
        risk_guard.release_order_slot(self.store, "005930.KS", "buy")
        self.assertTrue(risk_guard.acquire_order_slot(self.store, "005930.KS", "buy", 30))

    def test_cooldown_zero_always_allows(self):
        for _ in range(3):
            self.assertTrue(risk_guard.acquire_order_slot(self.store, "005930.KS", "buy", 0))

    def test_position_cap(self):
        qty, note = risk_guard.cap_buy_quantity(100, 10_000, 0, 10_000_000, 30)
        self.assertEqual((qty, note), (100, None))
        qty, note = risk_guard.cap_buy_quantity(400, 10_000, 0, 10_000_000, 30)
        self.assertEqual(qty, 300)
        self.assertIn("축소", note)
        qty, note = risk_guard.cap_buy_quantity(10, 10_000, 3_000_000, 10_000_000, 30)
        self.assertEqual(qty, 0)
        self.assertIn("도달", note)

    def test_daily_loss(self):
        self.assertTrue(risk_guard.daily_loss_breached(1_000_000, 960_000, 3.0))
        self.assertFalse(risk_guard.daily_loss_breached(1_000_000, 990_000, 3.0))
        self.assertFalse(risk_guard.daily_loss_breached(1_000_000, 100, 0))   # 한도 0 = 미사용
        self.assertEqual(risk_guard.day_start_equity(self.store, 1_000_000), 1_000_000)
        self.assertEqual(risk_guard.day_start_equity(self.store, 500_000), 1_000_000)  # 당일 고정

    def test_orders_today_counter(self):
        self.assertEqual(risk_guard.orders_today(self.store), 0)
        risk_guard.increment_orders_today(self.store)
        risk_guard.increment_orders_today(self.store)
        self.assertEqual(risk_guard.orders_today(self.store), 2)


class StrategyTests(unittest.TestCase):
    def test_universe_and_max_symbols(self):
        spec = {"universe": ["005930", "000660"], "position_sizing": {"max_symbols": 1}}
        self.assertEqual(strategy.apply_to_symbols(["005930.KS", "000660.KS", "035420.KS"], spec), ["005930.KS"])

    def test_empty_intersection_keeps_original(self):
        spec = {"universe": ["999999"]}
        self.assertEqual(strategy.apply_to_symbols(["005930.KS"], spec), ["005930.KS"])

    def test_threshold_judgement(self):
        spec = {"strategy_id": "t", "version": 1,
                "signal_weights": {"buy_threshold": 0.25, "sell_threshold": -0.25}}
        self.assertEqual(strategy.apply_to_signal({"score": 4}, spec)["action"], "강력 매수")
        self.assertEqual(strategy.apply_to_signal({"score": 2}, spec)["action"], "매수")
        self.assertEqual(strategy.apply_to_signal({"score": 0}, spec)["action"], "관망")
        self.assertEqual(strategy.apply_to_signal({"score": -4}, spec)["action"], "강력 매도")

    def test_rules_override_threshold(self):
        spec = {"strategy_id": "t", "version": 1,
                "signal_weights": {"buy_threshold": 0.25, "sell_threshold": -0.25},
                "entry": {"indicator": "ma_cross", "condition": "short_above_long",
                          "params": {"short_window": 2, "long_window": 4}},
                "exit": {"indicator": "ma_cross", "condition": "short_below_long",
                         "params": {"short_window": 2, "long_window": 4}}}
        rising = {"closes": [10.0, 11.0, 12.0, 13.0, 14.0]}
        out = strategy.apply_to_signal({"score": 0}, spec, None, rising)
        self.assertTrue(out["rule_based"])
        self.assertEqual(out["action"], "매수")
        falling = {"closes": [14.0, 13.0, 12.0, 11.0, 10.0]}
        self.assertEqual(strategy.apply_to_signal({"score": 4}, spec, None, falling)["action"], "매도")

    def test_shipped_specs_load(self):
        for name in strategy.available():
            self.assertIsNotNone(strategy.load(name), name)


class AggressiveTests(unittest.TestCase):
    def test_take_profit_sells_everything(self):
        s = Settings(aggressive_take_profit_pct=1.5, aggressive_stop_loss_pct=1.0,
                     aggressive_max_buys_per_cycle=1, aggressive_max_sells_per_cycle=2)
        out = aggressive.plan(s, {"A": {"signal": {"score": 2, "momentum_pct": 1.0}}},
                              ["A"], {"A": (10, 1_000.0)}, {"A": 1_100.0})
        self.assertEqual(out["sell"]["A"]["ratio"], 1.0)
        self.assertIn("익절", out["sell"]["A"]["reason"])

    def test_stop_loss_sells_everything(self):
        s = Settings(aggressive_take_profit_pct=1.5, aggressive_stop_loss_pct=1.0)
        out = aggressive.plan(s, {"A": {"signal": {"score": 2}}}, ["A"], {"A": (10, 1_000.0)}, {"A": 950.0})
        self.assertIn("손절", out["sell"]["A"]["reason"])

    def test_force_buy_rotation(self):
        s = Settings(aggressive_force_buy=True, aggressive_max_buys_per_cycle=1)
        out = aggressive.plan(s, {"A": {"signal": {"score": 0, "momentum_pct": 0.1}},
                                  "B": {"signal": {"score": 0, "momentum_pct": -0.5}}},
                              ["A", "B"], {}, {"A": 100.0, "B": 100.0})
        self.assertEqual(out["buy"], ["A"])
        self.assertTrue(out["notes"])

    def test_score_intraday_trend(self):
        closes = [100 + i * 0.5 for i in range(40)]
        sig = aggressive.score_intraday(closes, [1_000] * 40)
        self.assertGreaterEqual(sig["score"], 1)
        self.assertIn(sig["action"], ("매수", "강력 매수"))


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _trader(self, **kw) -> AutoTrader:
        s = _settings(self.dir, **kw)
        return AutoTrader(s, market=MarketData(s))

    def test_cycle_fills_and_persists(self):
        trader = self._trader()
        cycle = trader.run_cycle()
        filled = [t for t in cycle["trades"] if t["status"] == "filled"]
        self.assertTrue(filled)
        self.assertLess(trader.store.cash, trader.settings.initial_capital)
        self.assertEqual(len(trader.store.positions()), len(filled))
        self.assertEqual(cycle["risk"]["orders_today"], len(filled))

    def test_dry_run_changes_nothing(self):
        trader = self._trader()
        cycle = trader.run_cycle(dry_run=True)
        self.assertTrue([t for t in cycle["trades"] if t["status"] == "dry_run"])
        self.assertEqual(trader.store.cash, trader.settings.initial_capital)
        self.assertEqual(trader.store.positions(), {})

    def test_kill_switch_blocks_cycle(self):
        trader = self._trader()
        risk_guard.halt(trader.store, "테스트")
        cycle = trader.run_cycle()
        self.assertTrue(cycle["risk"]["halted"])
        self.assertEqual(cycle["trades"], [])

    def test_daily_loss_triggers_halt(self):
        trader = self._trader()
        # 당일 시작 자산을 현재 자산의 2배로 심어 손실 한도를 넘긴 상황을 만든다
        key = risk_guard.today_key()
        trader.store.risk.setdefault("day_start_equity", {})[key] = trader.settings.initial_capital * 2
        trader.store.save()
        cycle = trader.run_cycle()
        self.assertTrue(cycle["risk"]["halted"])
        self.assertTrue(trader.store.risk["kill_switch"])
        self.assertIn("일손실 한도", trader.store.risk["halt_reason"])

    def test_max_orders_per_day_limit(self):
        trader = self._trader(risk_max_orders_per_day=1)
        cycle = trader.run_cycle()
        self.assertEqual(len([t for t in cycle["trades"] if t["status"] == "filled"]), 1)
        self.assertTrue([r for r in cycle["risk"]["skipped"] if "일 주문 수 한도" in r["reason"]])

    def test_manual_symbols(self):
        trader = self._trader(symbol_source="manual", selected_symbols=["005930.KS"])
        cycle = trader.run_cycle()
        self.assertEqual(cycle["settings"]["symbols"], ["005930.KS"])

    def test_live_mode_sends_order_to_broker(self):
        class Spy:
            def __init__(self):
                self.orders = []

            def place_order(self, symbol, side, quantity, price, order_type="LIMIT"):
                self.orders.append((symbol, side, quantity, order_type))
                return {"status": "submitted", "order_no": "1"}

        spy = Spy()
        s = _settings(self.dir, mode="live", kis_enforce_market_hours=False)
        trader = AutoTrader(s, market=MarketData(s), broker=spy)
        cycle = trader.run_cycle()
        filled = [t for t in cycle["trades"] if t["status"] == "filled"]
        self.assertEqual(len(spy.orders), len(filled))
        self.assertTrue(all(t["live_order"]["status"] == "submitted" for t in filled))

    def test_live_order_skipped_outside_market_hours(self):
        class Boom:
            def place_order(self, *a, **k):
                raise AssertionError("장 운영시간 외에는 주문하지 않아야 한다")

        s = _settings(self.dir, mode="live", kis_enforce_market_hours=True)
        trader = AutoTrader(s, market=MarketData(s), broker=Boom())
        from lumina_local import market_data as md
        original = md.is_krx_market_open
        md.is_krx_market_open = lambda now=None: False
        try:
            import lumina_local.auto_trade as at
            at.is_krx_market_open = md.is_krx_market_open
            cycle = trader.run_cycle()
        finally:
            md.is_krx_market_open = original
            import lumina_local.auto_trade as at
            at.is_krx_market_open = original
        skipped = [t for t in cycle["trades"] if t.get("live_order", {}).get("reason") == "market_closed"]
        self.assertTrue(skipped)


class BacktestTests(unittest.TestCase):
    def test_backtest_returns_metrics(self):
        candles = mock_candles("005930.KS", "2y", "1d")["candles"]
        result = run_backtest("005930.KS", candles, None, initial_capital=10_000_000)
        self.assertGreater(result["bars"], 0)
        self.assertIn("return_pct", result)
        self.assertLessEqual(result["max_drawdown_pct"], 0)

    def test_backtest_needs_enough_bars(self):
        result = run_backtest("X", mock_candles("X", "1y", "1d")["candles"][:30], None)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
