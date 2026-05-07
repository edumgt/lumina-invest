"""CSV 데이터 인제스트: 개인CB, 기업CB, 금융상품."""
import csv
import os
from collections import defaultdict
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.config import settings


def _safe_float(v: str) -> float | None:
    try:
        f = float(v)
        return None if abs(f) > 1e14 else f
    except (ValueError, TypeError):
        return None


def _safe_int(v: str) -> int | None:
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


# ── 개인 CB ──────────────────────────────────────────────────────────
async def ingest_personal_cb(db: AsyncIOMotorDatabase, log: list[str]) -> int:
    data_dir = os.path.join(settings.DATA_DIR, "09.개인 CB정보")
    if not os.path.isdir(data_dir):
        log.append("[WARN] 개인CB 디렉토리 없음")
        return 0

    await db.personal_cb_stats.delete_many({})

    total_files, total_rows = 0, 0

    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".csv"):
            continue
        fpath = os.path.join(data_dir, fname)
        log.append(f"[개인CB] {fname} 처리 중...")

        agg: dict[tuple, dict] = defaultdict(lambda: {"cnt": 0, "sum_s": 0.0, "sum_s6": 0.0,
                                                       "sum_p1": 0.0, "sum_p2": 0.0})
        rows_in_file = 0

        with open(fpath, encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            header = next(reader)
            h = {c: i for i, c in enumerate(header)}
            idx = {
                "stdt": h.get("STDT", 0),
                "gender": h.get("GENDER", 2),
                "age_band": h.get("AGE_BAND", 3),
                "score": h.get("SCORE", len(header) - 6),
                "score_6m": h.get("SCORE_6M", len(header) - 5),
                "perf1": h.get("PERF1", len(header) - 4),
                "perf2": h.get("PERF2", len(header) - 3),
            }

            for row in reader:
                if len(row) < 4:
                    continue
                try:
                    stdt = row[idx["stdt"]]
                    gender = _safe_int(row[idx["gender"]])
                    age_band = _safe_int(row[idx["age_band"]])
                    score = _safe_float(row[idx["score"]])
                    score_6m = _safe_float(row[idx["score_6m"]])
                    perf1 = _safe_float(row[idx["perf1"]])
                    perf2 = _safe_float(row[idx["perf2"]])

                    k = (stdt, gender, age_band)
                    a = agg[k]
                    a["cnt"] += 1
                    if score is not None:
                        a["sum_s"] += score
                    if score_6m is not None:
                        a["sum_s6"] += score_6m
                    if perf1 is not None:
                        a["sum_p1"] += perf1
                    if perf2 is not None:
                        a["sum_p2"] += perf2
                    rows_in_file += 1
                except (IndexError, ValueError):
                    continue

        docs = []
        for (stdt, gender, age_band), a in agg.items():
            cnt = a["cnt"]
            if cnt == 0:
                continue
            docs.append({
                "stdt": stdt,
                "gender": gender,
                "age_band": age_band,
                "cnt": cnt,
                "avg_score": round(a["sum_s"] / cnt, 2),
                "avg_score_6m": round(a["sum_s6"] / cnt, 2),
                "default_rate_1": round(a["sum_p1"] / cnt, 6),
                "default_rate_2": round(a["sum_p2"] / cnt, 6),
            })
        if docs:
            await db.personal_cb_stats.insert_many(docs)

        log.append(f"  → {rows_in_file:,}행 처리 / 집계 {len(docs)}건 저장")
        total_rows += rows_in_file
        total_files += 1

    return total_rows


# ── 기업 CB ──────────────────────────────────────────────────────────
async def ingest_corporate_cb(db: AsyncIOMotorDatabase, log: list[str]) -> int:
    data_dir = os.path.join(settings.DATA_DIR, "10.기업 CB정보")
    if not os.path.isdir(data_dir):
        log.append("[WARN] 기업CB 디렉토리 없음")
        return 0

    await db.corporate_cb_stats.delete_many({})

    total_rows = 0

    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".csv"):
            continue
        fpath = os.path.join(data_dir, fname)
        log.append(f"[기업CB] {fname} 처리 중...")

        agg: dict[tuple, dict] = defaultdict(lambda: {"cnt": 0, "sum_g": 0.0, "sum_d": 0.0})
        rows_in_file = 0

        with open(fpath, encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            header = next(reader)
            h = {c: i for i, c in enumerate(header)}
            idx = {
                "bs_dt": h.get("BS_DT", 0),
                "sic_cd": h.get("SIC_CD_3", 2),
                "wg_gb": h.get("WG_GB", 3),
                "corp_grad": h.get("CORP_GRAD", len(header) - 2),
                "perf_12m": h.get("PERF_12M", len(header) - 1),
            }

            for row in reader:
                if len(row) < 4:
                    continue
                try:
                    bs_dt = row[idx["bs_dt"]]
                    sic_cd = row[idx["sic_cd"]]
                    wg_gb = _safe_int(row[idx["wg_gb"]])
                    corp_grad = _safe_float(row[idx["corp_grad"]])
                    perf_12m = _safe_float(row[idx["perf_12m"]])

                    k = (bs_dt, sic_cd, wg_gb)
                    a = agg[k]
                    a["cnt"] += 1
                    if corp_grad is not None and corp_grad < 100:
                        a["sum_g"] += corp_grad
                    if perf_12m is not None:
                        a["sum_d"] += perf_12m
                    rows_in_file += 1
                except (IndexError, ValueError):
                    continue

        docs = []
        for (bs_dt, sic_cd, wg_gb), a in agg.items():
            cnt = a["cnt"]
            if cnt == 0:
                continue
            docs.append({
                "bs_dt": bs_dt,
                "sic_cd": sic_cd,
                "wg_gb": wg_gb,
                "cnt": cnt,
                "avg_corp_grad": round(a["sum_g"] / cnt, 3),
                "default_rate": round(a["sum_d"] / cnt, 6),
            })
        if docs:
            await db.corporate_cb_stats.insert_many(docs)

        log.append(f"  → {rows_in_file:,}행 처리 / 집계 {len(docs)}건 저장")
        total_rows += rows_in_file

    return total_rows


# ── 금융상품 ─────────────────────────────────────────────────────────
async def ingest_bank_products(db: AsyncIOMotorDatabase, log: list[str]) -> int:
    fpath = os.path.join(settings.DATA_DIR, "12.금융상품정보", "은행수신상품.csv")
    if not os.path.exists(fpath):
        log.append("[WARN] 은행수신상품.csv 없음")
        return 0

    await db.bank_products.delete_many({})
    count = 0
    batch: list[dict] = []

    with open(fpath, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            batch.append({
                "bank_code": row.get("은행코드"),
                "bank_name": row.get("은행명"),
                "product_code": row.get("상품코드"),
                "product_name": row.get("상품명"),
                "product_group": row.get("상품그룹명"),
                "min_period": row.get("계약기간개월수_최소구간"),
                "max_period": row.get("계약기간개월수_최대구간"),
                "min_amount": row.get("가입금액_최소구간"),
                "max_amount": row.get("가입금액_최대구간"),
                "base_rate": _safe_float(row.get("기본금리", "")),
                "max_rate": _safe_float(row.get("최대우대금리", "")),
                "deposit_type": row.get("예금입출금방식"),
                "maturity": row.get("만기여부"),
                "deposit_protection": row.get("예금자보호대상여부"),
                "product_summary": (row.get("상품개요_설명") or "")[:500],
            })
            count += 1
            if len(batch) >= 1000:
                await db.bank_products.insert_many(batch)
                batch = []

    if batch:
        await db.bank_products.insert_many(batch)

    log.append(f"[은행상품] {count:,}건 저장")
    return count


async def ingest_fund_products(db: AsyncIOMotorDatabase, log: list[str]) -> int:
    fpath = os.path.join(settings.DATA_DIR, "12.금융상품정보", "공모펀드상품.csv")
    if not os.path.exists(fpath):
        log.append("[WARN] 공모펀드상품.csv 없음")
        return 0

    await db.fund_products.delete_many({})
    count = 0
    batch: list[dict] = []

    with open(fpath, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            batch.append({
                "eval_date": row.get("평가기준일"),
                "fund_code": row.get("펀드코드"),
                "fund_name": row.get("펀드명"),
                "company_name": row.get("운용사명"),
                "main_type": row.get("대유형"),
                "mid_type": row.get("중유형"),
                "sub_type": row.get("소유형"),
                "strategy": (row.get("투자전략") or "")[:300],
                "aum": _safe_float(row.get("순자산", "")),
                "risk_grade": _safe_int(row.get("투자위험등급", "")),
                "nav": _safe_float(row.get("펀드기준가", "")),
                "return_1y": _safe_float(row.get("펀드성과정보_1년", "")),
                "expense_ratio": _safe_float(row.get("운용보수", "")),
                "is_retirement": row.get("퇴직연금", "N") == "Y",
                "is_esg": row.get("ESG(사회책임투자형)", "N") == "Y",
            })
            count += 1
            if len(batch) >= 1000:
                await db.fund_products.insert_many(batch)
                batch = []

    if batch:
        await db.fund_products.insert_many(batch)

    log.append(f"[공모펀드] {count:,}건 저장")
    return count


async def run_full_ingest(db: AsyncIOMotorDatabase, log: list[str]) -> dict:
    log.append("=== 금융 데이터 인제스트 시작 ===")
    pcb = await ingest_personal_cb(db, log)
    ccb = await ingest_corporate_cb(db, log)
    bank = await ingest_bank_products(db, log)
    fund = await ingest_fund_products(db, log)
    log.append(f"=== 완료: 개인CB {pcb:,}행, 기업CB {ccb:,}행, 은행상품 {bank:,}건, 펀드 {fund:,}건 ===")
    return {"personal_cb_rows": pcb, "corporate_cb_rows": ccb,
            "bank_products": bank, "fund_products": fund}
