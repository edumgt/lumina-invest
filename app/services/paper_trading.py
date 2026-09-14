"""모의투자(Paper Trading) 서비스 — stock-coin-trade의 stock_trading / crypto / alternatives 이식.

세 자산군(국내주식·코인·대체자산)이 PaperAccount.cash(유저당 1행)를 공유한다.
모든 체결 함수는 호출자가 연 AsyncSession 안에서 실행되며, 계좌 행을 FOR UPDATE로 잠가
같은 사용자의 동시 주문이 잔고를 음수로 만들지 못하게 한다. commit은 호출자(라우트)가 한다.
"""
from __future__ import annotations

import asyncio
import math
import re
import time
import uuid
from datetime import date, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AlternativeOrder,
    AlternativePosition,
    CryptoHolding,
    CryptoOrder,
    Order,
    PaperAccount,
    Portfolio,
    PAPER_INITIAL_CASH,
)
from app.services.krx_companies import get_krx_companies
from app.services.stock import HEADERS, _yahoo_chart, get_candles, get_quote

BUY = "BUY"
SELL = "SELL"


class PaperTradeError(ValueError):
    """사용자에게 그대로 보여줄 수 있는 주문 실패 메시지."""


# ── 공용: 현금 계좌 ─────────────────────────────────────────────────────

async def get_account(db: AsyncSession, user_id: uuid.UUID, *, lock: bool = False) -> PaperAccount:
    stmt = select(PaperAccount).where(PaperAccount.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    account = (await db.execute(stmt)).scalar_one_or_none()
    if account is None:
        account = PaperAccount(user_id=user_id, cash=PAPER_INITIAL_CASH, initial_cash=PAPER_INITIAL_CASH)
        db.add(account)
        await db.flush()
        if lock:
            account = (await db.execute(stmt)).scalar_one()
    return account


async def apply_cash(db: AsyncSession, user_id: uuid.UUID, delta: float) -> PaperAccount:
    """현금 증감(음수면 차감). 잔고 부족 시 PaperTradeError."""
    account = await get_account(db, user_id, lock=True)
    if account.cash + delta < 0:
        raise PaperTradeError("보유 현금이 부족합니다.")
    account.cash = float(account.cash + delta)
    return account


async def reset_account(db: AsyncSession, user_id: uuid.UUID) -> PaperAccount:
    """주식·코인·대체자산 포지션과 주문 이력을 모두 지우고 초기 현금으로 되돌린다."""
    account = await get_account(db, user_id, lock=True)
    for model in (Portfolio, Order, CryptoHolding, CryptoOrder, AlternativePosition, AlternativeOrder):
        await db.execute(delete(model).where(model.user_id == user_id))
    account.cash = float(PAPER_INITIAL_CASH)
    account.initial_cash = float(PAPER_INITIAL_CASH)
    return account


# ── 국내주식 ─────────────────────────────────────────────────────────────

_STOCK_CACHE: dict[str, dict] = {}
_STOCK_TTL = 60


def normalize_stock_symbol(raw: str) -> str:
    """'005930' → '005930.KS'(KRX 목록으로 시장 확인), 'AAPL' → 'AAPL'."""
    return (raw or "").strip().upper()


async def resolve_stock(symbol: str) -> dict:
    """종목 메타 + 현재가. 6자리 코드는 KRX 상장법인 목록에서 종목명/시장을 찾는다.

    Returns {"symbol": "005930.KS", "code": "005930", "name": "삼성전자", "market": "KOSPI", "price": 74000}
    """
    symbol = normalize_stock_symbol(symbol)
    if not symbol:
        raise PaperTradeError("종목코드를 입력하세요.")
    now = time.time()
    cached = _STOCK_CACHE.get(symbol)
    if cached and now - cached["ts"] < _STOCK_TTL:
        return cached["data"]

    code = symbol.split(".")[0]
    meta: dict | None = None
    if re.fullmatch(r"[0-9A-Z]{6}", code):
        for c in await get_krx_companies():
            if c["code"] == code:
                meta = {"symbol": c["symbol"], "code": code, "name": c["name"], "market": c["market"]}
                break
    if meta is None and re.fullmatch(r"\d{6}", code):
        # KRX 목록을 못 받은 환경: KOSPI(.KS) → KOSDAQ(.KQ) 순서로 Yahoo에서 확인
        for suffix in ("KS", "KQ"):
            q = await get_quote(f"{code}.{suffix}")
            if q.get("price"):
                meta = {"symbol": f"{code}.{suffix}", "code": code, "name": q.get("name") or code,
                        "market": "KOSPI" if suffix == "KS" else "KOSDAQ"}
                break
    if meta is None:
        q = await get_quote(symbol)
        if not q.get("price"):
            raise PaperTradeError(f"지원하지 않는 종목입니다: {symbol}")
        meta = {"symbol": symbol, "code": code, "name": q.get("name") or symbol, "market": q.get("market") or "OTHER"}

    quote = await get_quote(meta["symbol"])
    price = quote.get("price")
    if not price:
        raise PaperTradeError("실시간 시세를 확인할 수 없어 주문할 수 없습니다. 잠시 후 다시 시도해주세요.")
    data = {**meta, "price": float(price), "prev_close": quote.get("prev_close"), "currency": quote.get("currency", "KRW")}
    _STOCK_CACHE[symbol] = {"ts": now, "data": data}
    _STOCK_CACHE[meta["symbol"]] = {"ts": now, "data": data}
    return data


def _price_unit(price: float) -> float:
    """원화 종목은 정수 단가, 그 외(USD 등)는 소수 2자리."""
    return float(int(price)) if price >= 1000 else round(price, 2)


async def _stock_position(db: AsyncSession, user_id: uuid.UUID, symbol: str) -> Portfolio | None:
    return (await db.execute(
        select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.symbol == symbol)
    )).scalar_one_or_none()


async def stock_positions(db: AsyncSession, user_id: uuid.UUID, include_volatility: bool = False) -> list[dict]:
    rows = (await db.execute(select(Portfolio).where(Portfolio.user_id == user_id))).scalars().all()
    result = []
    for pos in rows:
        price = pos.avg_price
        try:
            price = (await resolve_stock(pos.symbol))["price"]
        except Exception:
            pass
        eval_amount = pos.quantity * price
        item = {
            "symbol": pos.symbol, "name": pos.name, "quantity": pos.quantity,
            "avgPrice": pos.avg_price, "currentPrice": price, "evalAmount": eval_amount,
            "pnl": eval_amount - pos.quantity * pos.avg_price,
            "pnlRate": round((price - pos.avg_price) / pos.avg_price * 100, 2) if pos.avg_price else 0,
        }
        if include_volatility:
            item["volatility"] = await stock_volatility(pos.symbol)
        result.append(item)
    return result


async def stock_volatility(symbol: str) -> float | None:
    """최근 1개월 일봉 종가 기준 연환산(252거래일) 변동성(%)."""
    try:
        candles = (await get_candles(symbol, period="1mo", interval="1d")).get("candles", [])
        return annualized_volatility([c["close"] for c in candles if c.get("close")], 252)
    except Exception:
        return None


def annualized_volatility(closes: list[float], trading_periods: int = 252) -> float | None:
    if len(closes) < 3:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(trading_periods) * 100, 2)


async def stock_preview(db: AsyncSession, user_id: uuid.UUID, symbol: str, side: str, quantity: int) -> dict:
    side = (side or "").upper()
    if side not in (BUY, SELL) or not isinstance(quantity, int) or quantity <= 0:
        raise PaperTradeError("side(BUY/SELL)와 1 이상의 정수 quantity를 입력하세요.")
    info = await resolve_stock(symbol)
    price = _price_unit(info["price"])
    account = await get_account(db, user_id)
    position = await _stock_position(db, user_id, info["symbol"])
    held = position.quantity if position else 0
    amount = price * quantity
    executable = amount <= account.cash if side == BUY else quantity <= held
    return {
        "executable": executable, "symbol": info["symbol"], "name": info["name"], "side": side,
        "quantity": quantity, "estimatedPrice": price, "estimatedAmount": amount,
        "cashBefore": account.cash,
        "cashAfter": account.cash - amount if side == BUY else account.cash + amount,
        "positionBefore": held,
        "positionAfter": held + quantity if side == BUY else held - quantity,
        "reason": None if executable else ("보유 현금이 부족합니다." if side == BUY else "매도 가능 수량이 부족합니다."),
        "notice": "주문 미리보기는 체결·잔고를 변경하지 않으며, 실제 주문 시점 가격은 달라질 수 있습니다.",
    }


async def stock_order(db: AsyncSession, user_id: uuid.UUID, symbol: str, side: str, quantity: int,
                      source: str = "PAPER") -> dict:
    """시장가 모의 체결. 현금(PaperAccount) + 포지션(Portfolio) + 이력(Order)을 함께 갱신한다."""
    side = (side or "").upper()
    if side not in (BUY, SELL):
        raise PaperTradeError("side는 BUY 또는 SELL이어야 합니다.")
    if not isinstance(quantity, int) or quantity <= 0:
        raise PaperTradeError("quantity는 1 이상의 정수여야 합니다.")
    info = await resolve_stock(symbol)
    price = _price_unit(info["price"])
    amount = price * quantity

    account = await get_account(db, user_id, lock=True)
    position = await _stock_position(db, user_id, info["symbol"])

    if side == BUY:
        if amount > account.cash:
            raise PaperTradeError("보유 현금이 부족합니다.")
        account.cash = float(account.cash - amount)
        if position is None:
            db.add(Portfolio(user_id=user_id, symbol=info["symbol"], name=info["name"], quantity=quantity, avg_price=price))
        else:
            total_qty = position.quantity + quantity
            position.avg_price = (position.avg_price * position.quantity + amount) / total_qty
            position.quantity = total_qty
    else:
        if position is None or position.quantity < quantity:
            raise PaperTradeError("매도 가능한 수량이 부족합니다.")
        position.quantity -= quantity
        account.cash = float(account.cash + amount)
        if position.quantity == 0:
            await db.delete(position)

    db.add(Order(
        user_id=user_id, symbol=info["symbol"], name=info["name"], order_type=side.lower(),
        quantity=quantity, price=price, status="filled", broker="virtual", source=source,
    ))
    return {"status": "ok", "symbol": info["symbol"], "name": info["name"], "side": side,
            "quantity": quantity, "price": price, "amount": amount, "cash": account.cash}


async def stock_order_history(db: AsyncSession, user_id: uuid.UUID, limit: int = 50) -> list[dict]:
    rows = (await db.execute(
        select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc()).limit(limit)
    )).scalars().all()
    return [{
        "ts": int(o.created_at.timestamp() * 1000), "type": o.order_type.upper(), "symbol": o.symbol,
        "name": o.name, "quantity": o.quantity, "price": o.price, "amount": o.price * o.quantity,
        "source": o.source, "broker": o.broker,
    } for o in rows]


# ── 코인 (Upbit KRW 마켓) ────────────────────────────────────────────────

UPBIT_API = "https://api.upbit.com/v1"
_UPBIT_MARKETS: dict = {"ts": 0.0, "data": []}
_UPBIT_MARKET_TTL = 3600
_UPBIT_TICKER_CACHE: dict[str, dict] = {}
_UPBIT_TICKER_TTL = 5


async def upbit_markets() -> list[dict]:
    """KRW 마켓 목록 [{market, koreanName, englishName}] — 1시간 캐시."""
    now = time.time()
    if _UPBIT_MARKETS["data"] and now - _UPBIT_MARKETS["ts"] < _UPBIT_MARKET_TTL:
        return _UPBIT_MARKETS["data"]
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{UPBIT_API}/market/all", params={"isDetails": "false"})
            resp.raise_for_status()
            rows = resp.json()
        data = [{"market": r["market"], "koreanName": r.get("korean_name", ""), "englishName": r.get("english_name", "")}
                for r in rows if str(r.get("market", "")).startswith("KRW-")]
        if data:
            _UPBIT_MARKETS.update({"ts": now, "data": data})
            return data
    except Exception:
        pass
    return _UPBIT_MARKETS["data"] or [
        {"market": "KRW-BTC", "koreanName": "비트코인", "englishName": "Bitcoin"},
        {"market": "KRW-ETH", "koreanName": "이더리움", "englishName": "Ethereum"},
        {"market": "KRW-XRP", "koreanName": "리플", "englishName": "XRP"},
        {"market": "KRW-SOL", "koreanName": "솔라나", "englishName": "Solana"},
        {"market": "KRW-DOGE", "koreanName": "도지코인", "englishName": "Dogecoin"},
    ]


async def upbit_market_info(market_code: str) -> dict | None:
    code = (market_code or "").strip().upper()
    return next((m for m in await upbit_markets() if m["market"] == code), None)


async def upbit_tickers(codes: list[str]) -> list[dict]:
    """여러 마켓의 현재가/등락률/24h 거래대금."""
    codes = [c for c in codes if c]
    if not codes:
        return []
    now = time.time()
    missing = [c for c in codes if c not in _UPBIT_TICKER_CACHE or now - _UPBIT_TICKER_CACHE[c]["ts"] > _UPBIT_TICKER_TTL]
    if missing:
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(f"{UPBIT_API}/ticker", params={"markets": ",".join(missing)})
                resp.raise_for_status()
                for r in resp.json():
                    _UPBIT_TICKER_CACHE[r["market"]] = {"ts": now, "data": {
                        "market": r["market"], "price": float(r["trade_price"]),
                        "changeRate": round(float(r.get("signed_change_rate", 0)) * 100, 2),
                        "change": float(r.get("signed_change_price", 0)),
                        "high24h": float(r.get("high_price", 0)), "low24h": float(r.get("low_price", 0)),
                        "accTradePrice24h": float(r.get("acc_trade_price_24h", 0)),
                        "accTradeVolume24h": float(r.get("acc_trade_volume_24h", 0)),
                    }}
        except Exception:
            pass
    return [_UPBIT_TICKER_CACHE[c]["data"] for c in codes if c in _UPBIT_TICKER_CACHE]


async def upbit_trade_price(market_code: str) -> float:
    rows = await upbit_tickers([market_code])
    if not rows:
        raise PaperTradeError("현재가를 가져오지 못했습니다.")
    return rows[0]["price"]


async def upbit_candles(market_code: str, unit: str = "days", count: int = 120) -> list[dict]:
    """일봉/분봉 캔들 (Upbit). unit: days | minutes/60 | weeks"""
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(f"{UPBIT_API}/candles/{unit}", params={"market": market_code, "count": min(max(count, 1), 200)})
        resp.raise_for_status()
        rows = resp.json()
    rows.reverse()
    return [{
        "time": r["candle_date_time_kst"], "open": r["opening_price"], "high": r["high_price"],
        "low": r["low_price"], "close": r["trade_price"], "volume": r.get("candle_acc_trade_volume"),
    } for r in rows]


async def domestic_prices(market_code: str) -> dict:
    """국내 거래소 가격 비교 (Upbit / Bithumb / Korbit) — 실패한 거래소는 null."""
    symbol = market_code.split("-")[-1].upper()

    async def _fetch(url: str, extractor):
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url)
                if not resp.is_success:
                    return None
                return extractor(resp.json())
        except Exception:
            return None

    upbit, bithumb, korbit = await asyncio.gather(
        _fetch(f"{UPBIT_API}/ticker?markets=KRW-{symbol}", lambda d: float(d[0]["trade_price"])),
        _fetch(f"https://api.bithumb.com/public/ticker/{symbol}_KRW", lambda d: float(d["data"]["closing_price"])),
        _fetch(f"https://api.korbit.co.kr/v1/ticker?currency_pair={symbol.lower()}_krw", lambda d: float(d["last"])),
    )
    prices = {"upbit": upbit, "bithumb": bithumb, "korbit": korbit}
    valid = [p for p in prices.values() if p]
    return {"symbol": symbol, "prices": prices,
            "spreadPct": round((max(valid) - min(valid)) / min(valid) * 100, 3) if len(valid) >= 2 else None}


async def crypto_rankings(limit: int = 20) -> list[dict]:
    """24시간 거래대금 상위 KRW 마켓 (stock-coin-trade의 CoinMarketCap 랭킹 대체)."""
    markets = await upbit_markets()
    tickers = await upbit_tickers([m["market"] for m in markets[:120]])
    names = {m["market"]: m for m in markets}
    tickers.sort(key=lambda t: t["accTradePrice24h"], reverse=True)
    return [{**t, "koreanName": names.get(t["market"], {}).get("koreanName", ""),
             "englishName": names.get(t["market"], {}).get("englishName", ""),
             "symbol": t["market"].split("-")[-1]} for t in tickers[:limit]]


async def _crypto_holding(db: AsyncSession, user_id: uuid.UUID, market_code: str, lock: bool = False) -> CryptoHolding | None:
    stmt = select(CryptoHolding).where(CryptoHolding.user_id == user_id, CryptoHolding.market_code == market_code)
    if lock:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def crypto_holdings(db: AsyncSession, user_id: uuid.UUID) -> dict:
    account = await get_account(db, user_id)
    rows = (await db.execute(select(CryptoHolding).where(CryptoHolding.user_id == user_id))).scalars().all()
    tickers = {t["market"]: t for t in await upbit_tickers([r.market_code for r in rows])}
    hold_list, total_buy, total_eval = [], 0.0, 0.0
    for h in rows:
        price = tickers.get(h.market_code, {}).get("price") or h.avg_price
        eval_krw = round(h.quantity * price)
        hold_list.append({
            "marketCode": h.market_code, "symbol": h.market_code.split("-")[-1], "koreanName": h.korean_name,
            "holdCount": h.quantity, "buyAverage": h.avg_price, "buyTotalKrw": h.total_krw,
            "currentPrice": price, "evalKrw": eval_krw, "pnl": eval_krw - h.total_krw,
            "pnlRate": round((eval_krw - h.total_krw) / h.total_krw * 100, 2) if h.total_krw else 0,
        })
        total_buy += h.total_krw
        total_eval += eval_krw
    return {"memberAsset": account.cash, "totalBuyKrw": total_buy, "totalEvalKrw": total_eval,
            "holdCryptoList": hold_list, "marketArrayList": [h.market_code for h in rows]}


async def crypto_preview(db: AsyncSession, user_id: uuid.UUID, market_code: str, side: str,
                         buy_krw: float | None = None, sell_count: float | None = None) -> dict:
    side = (side or "").upper()
    market_code = (market_code or "").upper().strip()
    if side not in (BUY, SELL):
        raise PaperTradeError("side는 BUY 또는 SELL이어야 합니다.")
    market = await upbit_market_info(market_code)
    if not market:
        raise PaperTradeError("존재하지 않는 마켓입니다.")
    price = await upbit_trade_price(market_code)
    account = await get_account(db, user_id)
    held = await _crypto_holding(db, user_id, market_code)
    held_qty = held.quantity if held else 0.0
    if side == BUY:
        if not buy_krw or buy_krw <= 0:
            raise PaperTradeError("buyKrw는 0보다 커야 합니다.")
        amount = float(buy_krw)
        quantity = round(amount / price, 8)
        executable = amount <= account.cash
    else:
        if not sell_count or sell_count <= 0:
            raise PaperTradeError("sellCount는 0보다 커야 합니다.")
        quantity = float(sell_count)
        amount = round(quantity * price)
        executable = quantity <= held_qty
    return {"executable": executable, "marketCode": market_code, "koreanName": market["koreanName"], "side": side,
            "estimatedPrice": price, "quantity": quantity, "estimatedAmount": amount,
            "cashBefore": account.cash, "heldQuantity": held_qty,
            "reason": None if executable else ("보유 현금이 부족합니다." if side == BUY else "매도 가능 수량이 부족합니다."),
            "notice": "미리보기는 주문·보유자산을 변경하지 않으며 실제 체결 가격과 다를 수 있습니다."}


async def crypto_buy(db: AsyncSession, user_id: uuid.UUID, market_code: str, buy_krw: float, source: str = "WEB") -> dict:
    market_code = (market_code or "").upper().strip()
    if buy_krw <= 0:
        raise PaperTradeError("0보다 큰 수를 입력해주세요.")
    market = await upbit_market_info(market_code)
    if not market:
        raise PaperTradeError(f"존재하지 않는 마켓입니다: {market_code}")
    price = await upbit_trade_price(market_code)
    account = await get_account(db, user_id, lock=True)
    if buy_krw > account.cash:
        raise PaperTradeError("매수 가능 금액보다 클 수 없습니다.")
    quantity = round(buy_krw / price, 8)
    held = await _crypto_holding(db, user_id, market_code, lock=True)
    if held is None:
        db.add(CryptoHolding(user_id=user_id, market_code=market_code, korean_name=market["koreanName"],
                             quantity=quantity, avg_price=price, total_krw=buy_krw))
    else:
        total_count = round(held.quantity + quantity, 8)
        held.avg_price = round((held.avg_price * held.quantity + buy_krw) / total_count, 8)
        held.quantity = total_count
        held.total_krw = held.total_krw + buy_krw
    account.cash = float(account.cash - buy_krw)
    db.add(CryptoOrder(user_id=user_id, market_code=market_code, korean_name=market["koreanName"], order_type=BUY,
                       quantity=quantity, price=price, amount=buy_krw, source=source))
    return {"success": True, "asset": account.cash, "marketCode": market_code, "quantity": quantity, "price": price, "amount": buy_krw}


async def crypto_sell(db: AsyncSession, user_id: uuid.UUID, market_code: str, sell_count: float, source: str = "WEB") -> dict:
    market_code = (market_code or "").upper().strip()
    if sell_count <= 0:
        raise PaperTradeError("0보다 큰 수를 입력해주세요.")
    account = await get_account(db, user_id, lock=True)
    held = await _crypto_holding(db, user_id, market_code, lock=True)
    if held is None:
        raise PaperTradeError("암호화폐를 보유중이지 않습니다.")
    if held.quantity < sell_count:
        raise PaperTradeError("매도 가능 개수보다 클 수 없습니다.")
    price = await upbit_trade_price(market_code)
    # 매도 수량 비율만큼 매수 원금을 차감한다 (stock-coin-trade와 동일한 원가 배분).
    cost_out = round(held.total_krw * (sell_count / held.quantity))
    held.total_krw = held.total_krw - cost_out
    held.quantity = round(held.quantity - sell_count, 8)
    proceeds = round(price * sell_count)
    account.cash = float(account.cash + proceeds)
    if held.quantity <= 0:
        await db.delete(held)
    db.add(CryptoOrder(user_id=user_id, market_code=market_code, korean_name=held.korean_name, order_type=SELL,
                       quantity=sell_count, price=price, amount=proceeds, source=source))
    return {"success": True, "asset": account.cash, "marketCode": market_code, "quantity": sell_count, "price": price, "amount": proceeds}


async def crypto_history(db: AsyncSession, user_id: uuid.UUID, limit: int = 200) -> list[dict]:
    rows = (await db.execute(
        select(CryptoOrder).where(CryptoOrder.user_id == user_id).order_by(CryptoOrder.created_at.desc()).limit(limit)
    )).scalars().all()
    return [{"marketCode": r.market_code, "koreanName": r.korean_name, "type": r.order_type, "quantity": r.quantity,
             "price": r.price, "amount": r.amount, "source": r.source, "ts": int(r.created_at.timestamp() * 1000)}
            for r in rows]


# ── 대체자산 (선물·옵션·파생·금속·부동산) ────────────────────────────────

ALT_CATALOG = {
    "FUT-K200": {"name": "KOSPI 200 선물", "category": "선물", "price": 372_500, "multiplier": 1, "unit": "1계약", "marginRate": 15, "description": "KOSPI 200 지수 선물 축소 모의계약", "pointScale": 1_000, "actualMultiplier": 250_000},
    "FUT-USD": {"name": "미국 달러 선물", "category": "선물", "price": 13_850, "multiplier": 10, "unit": "10 USD", "marginRate": 12, "description": "원/달러 환율 선물 모의계약"},
    "OPT-K200-C": {"name": "KOSPI 200 콜옵션", "category": "옵션", "price": 12_800, "multiplier": 25, "unit": "1계약", "marginRate": 100, "description": "상승 전망을 연습하는 콜옵션"},
    "OPT-K200-P": {"name": "KOSPI 200 풋옵션", "category": "옵션", "price": 10_400, "multiplier": 25, "unit": "1계약", "marginRate": 100, "description": "하락 위험 헤지를 연습하는 풋옵션"},
    "DRV-LEV": {"name": "KOSPI 200 레버리지", "category": "파생상품", "price": 18_450, "multiplier": 1, "unit": "1좌", "marginRate": 100, "description": "지수 수익률 2배 추종형 모의 ETN"},
    "DRV-INV": {"name": "KOSPI 200 인버스", "category": "파생상품", "price": 7_920, "multiplier": 1, "unit": "1좌", "marginRate": 100, "description": "지수 하락 방향 모의 ETN"},
    "MET-GOLD": {"name": "금 (순금 99.99%)", "category": "금", "price": 178_300, "multiplier": 1, "unit": "1g", "marginRate": 100, "description": "국내 금 현물 기준 모의가격"},
    "MET-SILVER": {"name": "은 (99.9%)", "category": "은", "price": 2_480, "multiplier": 1, "unit": "1g", "marginRate": 100, "description": "국내 은 현물 기준 모의가격"},
    "RE-SEOUL": {"name": "서울 강남 아파트 지분", "category": "부동산", "price": 12_850_000, "multiplier": 1, "unit": "1구좌", "marginRate": 100, "description": "전용 84㎡ 대표 단지 시세를 분할한 교육용 지분", "location": {"lat": 37.4979, "lng": 127.0276, "label": "서울 강남구"}},
    "RE-PANGYO": {"name": "판교 아파트 지분", "category": "부동산", "price": 9_720_000, "multiplier": 1, "unit": "1구좌", "marginRate": 100, "description": "전용 84㎡ 대표 단지 시세를 분할한 교육용 지분", "location": {"lat": 37.3947, "lng": 127.1112, "label": "경기 성남시 판교"}},
    "RE-BUSAN": {"name": "부산 해운대 아파트 지분", "category": "부동산", "price": 6_380_000, "multiplier": 1, "unit": "1구좌", "marginRate": 100, "description": "전용 84㎡ 대표 단지 시세를 분할한 교육용 지분", "location": {"lat": 35.1631, "lng": 129.1636, "label": "부산 해운대구"}},
}

# Yahoo Finance 지연 시세를 실제 원천으로 쓰는 상품. 옵션·부동산은 공개 실시간 원천이 없어 교육용 기준가를 유지한다.
ALT_LIVE_FEEDS = {
    "FUT-K200": {"ticker": "^KS200", "scale": 1_000, "label": "KOSPI 200 지수 연동 (Yahoo Finance 지연 시세)"},
    "FUT-USD": {"ticker": "KRW=X", "scale": 10, "label": "원/달러 환율 연동 (Yahoo Finance 지연 시세)"},
    "DRV-LEV": {"ticker": "122630.KS", "scale": 1, "label": "KODEX 레버리지 (Yahoo Finance 지연 시세)"},
    "DRV-INV": {"ticker": "114800.KS", "scale": 1, "label": "KODEX 인버스 (Yahoo Finance 지연 시세)"},
    "MET-GOLD": {"ticker": "GC=F", "scale": 1, "label": "국제 금 선물 참고 (Yahoo Finance 지연 시세)"},
    "MET-SILVER": {"ticker": "SI=F", "scale": 1, "label": "국제 은 선물 참고 (Yahoo Finance 지연 시세)"},
}
_ALT_LIVE_TTL = 60
_alt_live_cache: dict[str, dict] = {}


async def _alt_feed_scale(symbol: str) -> float:
    """원화/g로 표시하는 금속 상품에는 최신 USD/KRW 환율을 적용한다 (troy oz → g)."""
    feed = ALT_LIVE_FEEDS[symbol]
    if symbol not in {"MET-GOLD", "MET-SILVER"}:
        return feed["scale"]
    try:
        q = await get_quote("KRW=X")
        return float(q["price"]) / 31.1035
    except Exception:
        return 1.0


async def _alt_live_chart(symbol: str, days: int = 365) -> list[dict]:
    if symbol not in ALT_LIVE_FEEDS:
        return []
    now = time.time()
    cached = _alt_live_cache.get(symbol)
    if cached and now - cached["ts"] < _ALT_LIVE_TTL:
        return cached["data"][-days:]
    try:
        raw = await _yahoo_chart(ALT_LIVE_FEEDS[symbol]["ticker"], "1d", "2y")
        scale = await _alt_feed_scale(symbol)
        data = []
        if raw:
            quote = raw.get("indicators", {}).get("quote", [{}])[0]
            for i, ts in enumerate(raw.get("timestamp", [])):
                vals = [quote.get(k, [None] * (i + 1))[i] for k in ("open", "high", "low", "close")]
                if any(v is None or not math.isfinite(v) or v <= 0 for v in vals):
                    continue
                o, h, l, c = [v * scale for v in vals]
                data.append({"time": date.fromtimestamp(ts).isoformat(), "open": round(o), "high": round(h), "low": round(l), "close": round(c)})
        if len(data) >= 2:
            _alt_live_cache[symbol] = {"ts": now, "data": data}
            return data[-days:]
    except Exception:
        pass
    return cached["data"][-days:] if cached else []


async def alt_quote(symbol: str) -> dict:
    item = ALT_CATALOG[symbol]
    live = await _alt_live_chart(symbol, 2)
    if len(live) >= 2:
        price, previous = live[-1]["close"], live[-2]["close"]
        rate = round((price - previous) / previous * 100, 2) if previous else 0
        source, updated_at = ALT_LIVE_FEEDS[symbol]["label"], live[-1]["time"]
    else:
        wave = math.sin(date.today().toordinal() * 0.71 + sum(map(ord, symbol)))
        rate = round(wave * (0.018 if item["category"] in {"옵션", "파생상품"} else 0.009), 2)
        price = max(1, round(item["price"] * (1 + rate / 100)))
        source, updated_at = "교육용 기준 시세", date.today().isoformat()
    notional = price * item["multiplier"]
    margin = round(notional * item["marginRate"] / 100)
    quote = {"symbol": symbol, **item, "price": price, "changeRate": rate, "notionalPerUnit": notional,
             "tradeAmountPerUnit": margin, "updatedAt": updated_at, "source": source}
    if item.get("pointScale"):
        quote["actualPoint"] = price / item["pointScale"]
    return quote


async def alt_chart(symbol: str, days: int = 120) -> list[dict]:
    days = max(30, min(days, 365))
    live = await _alt_live_chart(symbol, days)
    if len(live) >= 2:
        return live
    quote = await alt_quote(symbol)
    raw = []
    for index in range(days):
        day = date.today() - timedelta(days=days - index - 1)
        seed = sum(map(ord, symbol)) + day.toordinal()
        center = 1 + math.sin(seed * .17) * .035 + math.cos(seed * .043) * .018
        opening = center * (1 + math.sin(seed * .31) * .009)
        closing = center * (1 + math.cos(seed * .23) * .011)
        high = max(opening, closing) * (1.006 + abs(math.sin(seed)) * .012)
        low = min(opening, closing) * (1 - .006 - abs(math.cos(seed)) * .01)
        raw.append((day.isoformat(), opening, high, low, closing))
    scale = quote["price"] / raw[-1][4]
    return [{"time": r[0], "open": round(r[1] * scale), "high": round(r[2] * scale), "low": round(r[3] * scale), "close": round(r[4] * scale)} for r in raw]


async def alt_positions(db: AsyncSession, user_id: uuid.UUID, include_volatility: bool = False) -> list[dict]:
    rows = (await db.execute(select(AlternativePosition).where(AlternativePosition.user_id == user_id))).scalars().all()
    result = []
    for row in rows:
        quote = await alt_quote(row.symbol)
        # 선물은 계약 명목금액이 아니라 주문 시 납부한 증거금을 기준으로 손익을 표시한다.
        cost = round(row.avg_price * quote["multiplier"] * quote["marginRate"] / 100) * row.quantity
        value = quote["tradeAmountPerUnit"] * row.quantity
        item = {"symbol": row.symbol, "name": quote["name"], "category": row.category, "quantity": row.quantity,
                "avgPrice": row.avg_price, "currentPrice": quote["price"], "multiplier": quote["multiplier"],
                "unit": quote["unit"], "evalAmount": value, "pnl": value - cost, "marginRate": quote["marginRate"]}
        if include_volatility:
            item["volatility"] = annualized_volatility([c["close"] for c in await alt_chart(row.symbol, 30)], 252)
        result.append(item)
    return result


async def alt_preview(db: AsyncSession, user_id: uuid.UUID, symbol: str, side: str, quantity: int) -> dict:
    symbol, side = (symbol or "").upper(), (side or "").upper()
    if symbol not in ALT_CATALOG or side not in (BUY, SELL) or quantity <= 0:
        raise PaperTradeError("상품, BUY/SELL, 1 이상의 수량을 확인해주세요.")
    quote = await alt_quote(symbol)
    account = await get_account(db, user_id)
    position = (await db.execute(select(AlternativePosition).where(
        AlternativePosition.user_id == user_id, AlternativePosition.symbol == symbol))).scalar_one_or_none()
    held = position.quantity if position else 0
    amount = quote["tradeAmountPerUnit"] * quantity
    executable = amount <= account.cash if side == BUY else quantity <= held
    return {"executable": executable, "symbol": symbol, "name": quote["name"], "category": quote["category"], "side": side,
            "quantity": quantity, "estimatedPrice": quote["price"], "estimatedAmount": amount, "marginRate": quote["marginRate"],
            "cashBefore": account.cash, "heldQuantity": held,
            "reason": None if executable else ("보유 현금이 부족합니다." if side == BUY else "매도 가능 수량이 부족합니다."),
            "notice": "미리보기는 교육용 기준 시세를 사용하며 주문·잔고를 변경하지 않습니다."}


async def alt_order(db: AsyncSession, user_id: uuid.UUID, symbol: str, side: str, quantity: int, source: str = "WEB") -> dict:
    symbol, side = (symbol or "").upper(), (side or "").upper()
    if symbol not in ALT_CATALOG or side not in (BUY, SELL) or quantity <= 0:
        raise PaperTradeError("상품, 매수/매도 구분, 1 이상의 수량을 확인해주세요.")
    quote = await alt_quote(symbol)
    amount = quote["tradeAmountPerUnit"] * quantity
    account = await get_account(db, user_id, lock=True)
    position = (await db.execute(select(AlternativePosition).where(
        AlternativePosition.user_id == user_id, AlternativePosition.symbol == symbol).with_for_update())).scalar_one_or_none()
    if side == BUY:
        if account.cash < amount:
            raise PaperTradeError("보유 현금이 부족합니다.")
        account.cash = float(account.cash - amount)
        if position is None:
            db.add(AlternativePosition(user_id=user_id, symbol=symbol, category=quote["category"], quantity=quantity, avg_price=quote["price"]))
        else:
            total_qty = position.quantity + quantity
            position.avg_price = round((position.avg_price * position.quantity + quote["price"] * quantity) / total_qty)
            position.quantity = total_qty
    else:
        if position is None or position.quantity < quantity:
            raise PaperTradeError("매도 가능한 보유 수량이 부족합니다.")
        position.quantity -= quantity
        account.cash = float(account.cash + amount)
        if position.quantity == 0:
            await db.delete(position)
    db.add(AlternativeOrder(user_id=user_id, symbol=symbol, name=quote["name"], category=quote["category"], order_type=side,
                            quantity=quantity, price=quote["price"], multiplier=quote["multiplier"], amount=amount, source=source))
    return {"status": "ok", "cash": account.cash, "symbol": symbol, "side": side, "quantity": quantity, "price": quote["price"], "amount": amount}


async def alt_history(db: AsyncSession, user_id: uuid.UUID, limit: int = 100) -> list[dict]:
    rows = (await db.execute(
        select(AlternativeOrder).where(AlternativeOrder.user_id == user_id).order_by(AlternativeOrder.created_at.desc()).limit(limit)
    )).scalars().all()
    return [{"symbol": r.symbol, "name": r.name, "category": r.category, "type": r.order_type, "quantity": r.quantity,
             "price": r.price, "amount": r.amount, "source": r.source, "ts": int(r.created_at.timestamp() * 1000)} for r in rows]


# ── 계좌 스냅샷 (세 자산군 합산) ─────────────────────────────────────────

async def account_snapshot(db: AsyncSession, user_id: uuid.UUID) -> dict:
    account = await get_account(db, user_id)
    stocks = await stock_positions(db, user_id)
    crypto = await crypto_holdings(db, user_id)
    alts = await alt_positions(db, user_id)
    stock_eval = sum(p["evalAmount"] for p in stocks)
    crypto_eval = crypto["totalEvalKrw"]
    alt_eval = sum(p["evalAmount"] for p in alts)
    total_asset = account.cash + stock_eval + crypto_eval + alt_eval
    initial = account.initial_cash or PAPER_INITIAL_CASH
    return {
        "cash": account.cash, "initialCash": initial,
        "stockEval": stock_eval, "cryptoEval": crypto_eval, "alternativeEval": alt_eval,
        "totalAsset": total_asset,
        "totalPnl": total_asset - initial,
        "totalPnlRate": round((total_asset - initial) / initial * 100, 4) if initial else 0,
        "counts": {"stocks": len(stocks), "crypto": len(crypto["holdCryptoList"]), "alternatives": len(alts)},
    }
