"""MongoDB-based financial data query tools for the ReAct agent."""
from typing import Any
from motor.motor_asyncio import AsyncIOMotorDatabase

GENDER_MAP = {1: "남성", 2: "여성"}
AGE_MAP = {
    1: "10대이하", 2: "20대", 3: "30대",
    4: "40대", 5: "50대", 6: "60대이상",
}
SIZE_MAP = {1: "대기업", 2: "중견기업", 3: "중소기업"}
INDUSTRY_MAP = {
    "A": "농업/임업/어업", "B": "광업", "C": "제조업",
    "D": "전기/가스", "E": "수도/환경", "F": "건설업",
    "G": "도소매업", "H": "운수/창고", "I": "숙박/음식",
    "J": "정보통신업", "K": "금융/보험", "L": "부동산업",
    "M": "전문/과학/기술", "N": "사업지원", "O": "공공행정",
    "P": "교육서비스", "Q": "보건/사회복지", "R": "예술/스포츠",
    "S": "기타서비스",
}


def _industry_label(sic_cd: str | None) -> str:
    if not sic_cd:
        return "미분류"
    return INDUSTRY_MAP.get(sic_cd[0], sic_cd[0])


async def query_personal_cb(db: AsyncIOMotorDatabase, args: dict[str, Any]) -> str:
    """개인 CB 신용 통계 조회."""
    match: dict = {}
    if p := args.get("period"):
        match["stdt"] = str(p)
    if g := args.get("gender"):
        match["gender"] = int(g)
    if a := args.get("age_band"):
        match["age_band"] = int(a)

    group_by_str = args.get("group_by", "stdt,gender,age_band")
    group_fields = [f.strip() for f in group_by_str.split(",")]
    group_id = {f: f"${f}" for f in group_fields}

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": group_id,
            "total": {"$sum": "$cnt"},
            "avg_score": {"$avg": "$avg_score"},
            "avg_score_6m": {"$avg": "$avg_score_6m"},
            "default_pct_raw": {"$avg": "$default_rate_1"},
        }},
        {"$project": {
            "_id": 0,
            **{f: f"$_id.{f}" for f in group_fields},
            "total": 1,
            "avg_score": {"$round": ["$avg_score", 1]},
            "avg_score_6m": {"$round": ["$avg_score_6m", 1]},
            "default_pct": {"$round": [{"$multiply": ["$default_pct_raw", 100]}, 2]},
        }},
        {"$sort": {"stdt": -1, "age_band": 1}},
        {"$limit": 50},
    ]

    rows = await db.personal_cb_stats.aggregate(pipeline).to_list(length=50)

    if not rows:
        return "조회된 개인 CB 데이터가 없습니다. 먼저 데이터를 인제스트해주세요."

    lines = ["[개인 CB 신용 통계]"]
    for r in rows:
        g_label = GENDER_MAP.get(r.get("gender"), str(r.get("gender")))
        a_label = AGE_MAP.get(r.get("age_band"), str(r.get("age_band")))
        lines.append(
            f"기준월:{r.get('stdt')} | {g_label}/{a_label} | "
            f"인원:{r.get('total', 0):,}명 | 평균신용점수:{r.get('avg_score')} | "
            f"6개월전:{r.get('avg_score_6m')} | 연체율:{r.get('default_pct')}%"
        )
    return "\n".join(lines)


async def query_corporate_cb(db: AsyncIOMotorDatabase, args: dict[str, Any]) -> str:
    """기업 CB 신용 통계 조회."""
    match: dict = {}
    if p := args.get("period"):
        match["bs_dt"] = {"$regex": f"^{p}"}
    if s := args.get("sic_cd"):
        match["sic_cd"] = {"$regex": f"^{s}"}
    if w := args.get("wg_gb"):
        match["wg_gb"] = int(w)

    group_by_str = args.get("group_by", "bs_dt,sic_cd,wg_gb")
    group_fields = [f.strip() for f in group_by_str.split(",")]
    group_id = {f: f"${f}" for f in group_fields}

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": group_id,
            "total": {"$sum": "$cnt"},
            "avg_grade": {"$avg": "$avg_corp_grad"},
            "default_pct_raw": {"$avg": "$default_rate"},
        }},
        {"$project": {
            "_id": 0,
            **{f: f"$_id.{f}" for f in group_fields},
            "total": 1,
            "avg_grade": {"$round": ["$avg_grade", 2]},
            "default_pct": {"$round": [{"$multiply": ["$default_pct_raw", 100]}, 2]},
        }},
        {"$sort": {"bs_dt": -1, "sic_cd": 1}},
        {"$limit": 50},
    ]

    rows = await db.corporate_cb_stats.aggregate(pipeline).to_list(length=50)

    if not rows:
        return "조회된 기업 CB 데이터가 없습니다. 먼저 데이터를 인제스트해주세요."

    lines = ["[기업 CB 신용 통계]"]
    for r in rows:
        w_label = SIZE_MAP.get(r.get("wg_gb"), str(r.get("wg_gb")))
        ind_label = _industry_label(r.get("sic_cd"))
        lines.append(
            f"기준일:{r.get('bs_dt')} | {w_label}/{ind_label} | "
            f"기업수:{r.get('total', 0):,}개 | 평균신용등급:{r.get('avg_grade')} | "
            f"연체율:{r.get('default_pct')}%"
        )
    return "\n".join(lines)


async def search_bank_products(db: AsyncIOMotorDatabase, args: dict[str, Any]) -> str:
    """은행 수신상품 검색."""
    query: dict = {}
    limit = min(int(args.get("limit", 10)), 20)

    if min_rate := args.get("min_rate"):
        query["base_rate"] = {"$gte": float(min_rate)}
    if bank := args.get("bank_name"):
        query["bank_name"] = {"$regex": bank, "$options": "i"}
    if dtype := args.get("deposit_type"):
        query["deposit_type"] = {"$regex": dtype, "$options": "i"}
    if pg := args.get("product_group"):
        query["product_group"] = {"$regex": pg, "$options": "i"}
    if keyword := args.get("keyword"):
        query["$or"] = [
            {"product_name": {"$regex": keyword, "$options": "i"}},
            {"product_summary": {"$regex": keyword, "$options": "i"}},
        ]

    projection = {"_id": 0, "bank_name": 1, "product_name": 1, "product_group": 1,
                  "min_period": 1, "max_period": 1, "base_rate": 1, "max_rate": 1,
                  "deposit_type": 1, "deposit_protection": 1, "product_summary": 1}

    rows = await db.bank_products.find(query, projection).sort(
        "base_rate", -1
    ).limit(limit).to_list(length=limit)

    if not rows:
        return "조건에 맞는 은행 수신상품이 없습니다."

    lines = [f"[은행 수신상품 검색 결과 - {len(rows)}건]"]
    for r in rows:
        lines.append(
            f"■ {r.get('bank_name')} | {r.get('product_name')} ({r.get('product_group')})\n"
            f"  기간:{r.get('min_period')}~{r.get('max_period')} | "
            f"기본금리:{r.get('base_rate')}% | 최대금리:{r.get('max_rate')}% | "
            f"예금자보호:{r.get('deposit_protection')} | "
            f"상품유형:{r.get('deposit_type')}"
        )
    return "\n".join(lines)


async def search_funds(db: AsyncIOMotorDatabase, args: dict[str, Any]) -> str:
    """공모펀드 검색."""
    query: dict = {}
    limit = min(int(args.get("limit", 10)), 20)

    if mt := args.get("main_type"):
        query["main_type"] = {"$regex": mt, "$options": "i"}
    if rg := args.get("max_risk_grade"):
        query["risk_grade"] = {"$lte": int(rg)}
    if mr := args.get("min_return_1y"):
        query["return_1y"] = {"$gte": float(mr)}
    if args.get("is_retirement"):
        query["is_retirement"] = True
    if args.get("is_esg"):
        query["is_esg"] = True
    if keyword := args.get("keyword"):
        query["$or"] = [
            {"fund_name": {"$regex": keyword, "$options": "i"}},
            {"company_name": {"$regex": keyword, "$options": "i"}},
            {"strategy": {"$regex": keyword, "$options": "i"}},
        ]

    projection = {"_id": 0, "fund_name": 1, "company_name": 1, "main_type": 1,
                  "mid_type": 1, "risk_grade": 1, "return_1y": 1,
                  "expense_ratio": 1, "aum": 1, "is_retirement": 1, "is_esg": 1}

    rows = await db.fund_products.find(query, projection).sort(
        "return_1y", -1
    ).limit(limit).to_list(length=limit)

    if not rows:
        return "조건에 맞는 펀드 상품이 없습니다."

    lines = [f"[공모펀드 검색 결과 - {len(rows)}건]"]
    for r in rows:
        retire = "✓퇴직연금" if r.get("is_retirement") else ""
        esg = "✓ESG" if r.get("is_esg") else ""
        aum = r.get("aum") or 0
        lines.append(
            f"■ {r.get('fund_name')} ({r.get('company_name')})\n"
            f"  유형:{r.get('main_type')}/{r.get('mid_type')} | "
            f"위험등급:{r.get('risk_grade')} | 1년수익률:{r.get('return_1y')}% | "
            f"운용보수:{r.get('expense_ratio')}% | 순자산:{aum:,.0f}원 {retire}{esg}"
        )
    return "\n".join(lines)
