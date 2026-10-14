"""PostgreSQL-based financial data query tools for the ReAct agent."""
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BankProduct, CorporateCbStat, FundProduct, PersonalCbStat

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

_PCB_COLUMNS = {"stdt": PersonalCbStat.stdt, "gender": PersonalCbStat.gender, "age_band": PersonalCbStat.age_band}
_CCB_COLUMNS = {"bs_dt": CorporateCbStat.bs_dt, "sic_cd": CorporateCbStat.sic_cd, "wg_gb": CorporateCbStat.wg_gb}


def _industry_label(sic_cd: str | None) -> str:
    if not sic_cd:
        return "미분류"
    return INDUSTRY_MAP.get(sic_cd[0], sic_cd[0])


async def query_personal_cb(db: AsyncSession, args: dict[str, Any]) -> str:
    """개인 CB 신용 통계 조회."""
    conditions = []
    if p := args.get("period"):
        conditions.append(PersonalCbStat.stdt == str(p))
    if g := args.get("gender"):
        conditions.append(PersonalCbStat.gender == int(g))
    if a := args.get("age_band"):
        conditions.append(PersonalCbStat.age_band == int(a))

    group_by_str = args.get("group_by", "stdt,gender,age_band")
    group_fields = [f.strip() for f in group_by_str.split(",") if f.strip() in _PCB_COLUMNS]
    if not group_fields:
        group_fields = ["stdt", "gender", "age_band"]
    group_cols = [_PCB_COLUMNS[f] for f in group_fields]

    stmt = select(
        *group_cols,
        func.sum(PersonalCbStat.cnt).label("total"),
        func.avg(PersonalCbStat.avg_score).label("avg_score"),
        func.avg(PersonalCbStat.avg_score_6m).label("avg_score_6m"),
        func.avg(PersonalCbStat.default_rate_1).label("default_pct_raw"),
    ).group_by(*group_cols).limit(50)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    order_cols = []
    if "stdt" in group_fields:
        order_cols.append(PersonalCbStat.stdt.desc())
    if "age_band" in group_fields:
        order_cols.append(PersonalCbStat.age_band.asc())
    if order_cols:
        stmt = stmt.order_by(*order_cols)

    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return "조회된 개인 CB 데이터가 없습니다. 먼저 데이터를 인제스트해주세요."

    lines = ["[개인 CB 신용 통계]"]
    for r in rows:
        gender_val = getattr(r, "gender", None)
        age_band_val = getattr(r, "age_band", None)
        stdt_val = getattr(r, "stdt", "-")
        g_label = GENDER_MAP.get(gender_val, str(gender_val)) if gender_val is not None else "전체"
        a_label = AGE_MAP.get(age_band_val, str(age_band_val)) if age_band_val is not None else "전체"
        avg_score = round(r.avg_score, 1) if r.avg_score is not None else None
        avg_score_6m = round(r.avg_score_6m, 1) if r.avg_score_6m is not None else None
        default_pct = round(r.default_pct_raw * 100, 2) if r.default_pct_raw is not None else None
        lines.append(
            f"기준월:{stdt_val} | {g_label}/{a_label} | "
            f"인원:{r.total or 0:,}명 | 평균신용점수:{avg_score} | "
            f"6개월전:{avg_score_6m} | 연체율:{default_pct}%"
        )
    return "\n".join(lines)


async def query_corporate_cb(db: AsyncSession, args: dict[str, Any]) -> str:
    """기업 CB 신용 통계 조회."""
    conditions = []
    if p := args.get("period"):
        conditions.append(CorporateCbStat.bs_dt.like(f"{p}%"))
    if s := args.get("sic_cd"):
        conditions.append(CorporateCbStat.sic_cd.like(f"{s}%"))
    if w := args.get("wg_gb"):
        conditions.append(CorporateCbStat.wg_gb == int(w))

    group_by_str = args.get("group_by", "bs_dt,sic_cd,wg_gb")
    group_fields = [f.strip() for f in group_by_str.split(",") if f.strip() in _CCB_COLUMNS]
    if not group_fields:
        group_fields = ["bs_dt", "sic_cd", "wg_gb"]
    group_cols = [_CCB_COLUMNS[f] for f in group_fields]

    stmt = select(
        *group_cols,
        func.sum(CorporateCbStat.cnt).label("total"),
        func.avg(CorporateCbStat.avg_corp_grad).label("avg_grade"),
        func.avg(CorporateCbStat.default_rate).label("default_pct_raw"),
    ).group_by(*group_cols).limit(50)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    order_cols = []
    if "bs_dt" in group_fields:
        order_cols.append(CorporateCbStat.bs_dt.desc())
    if "sic_cd" in group_fields:
        order_cols.append(CorporateCbStat.sic_cd.asc())
    if order_cols:
        stmt = stmt.order_by(*order_cols)

    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return "조회된 기업 CB 데이터가 없습니다. 먼저 데이터를 인제스트해주세요."

    lines = ["[기업 CB 신용 통계]"]
    for r in rows:
        wg_gb_val = getattr(r, "wg_gb", None)
        sic_cd_val = getattr(r, "sic_cd", None)
        bs_dt_val = getattr(r, "bs_dt", "-")
        w_label = SIZE_MAP.get(wg_gb_val, str(wg_gb_val)) if wg_gb_val is not None else "전체"
        ind_label = _industry_label(sic_cd_val)
        avg_grade = round(r.avg_grade, 2) if r.avg_grade is not None else None
        default_pct = round(r.default_pct_raw * 100, 2) if r.default_pct_raw is not None else None
        lines.append(
            f"기준일:{bs_dt_val} | {w_label}/{ind_label} | "
            f"기업수:{r.total or 0:,}개 | 평균신용등급:{avg_grade} | "
            f"연체율:{default_pct}%"
        )
    return "\n".join(lines)


async def search_bank_products(db: AsyncSession, args: dict[str, Any]) -> str:
    """은행 수신상품 검색."""
    conditions = []
    limit = min(int(args.get("limit", 10)), 20)

    if min_rate := args.get("min_rate"):
        conditions.append(BankProduct.base_rate >= float(min_rate))
    if bank := args.get("bank_name"):
        conditions.append(BankProduct.bank_name.ilike(f"%{bank}%"))
    if dtype := args.get("deposit_type"):
        conditions.append(BankProduct.deposit_type.ilike(f"%{dtype}%"))
    if pg := args.get("product_group"):
        conditions.append(BankProduct.product_group.ilike(f"%{pg}%"))
    if keyword := args.get("keyword"):
        conditions.append(or_(
            BankProduct.product_name.ilike(f"%{keyword}%"),
            BankProduct.product_summary.ilike(f"%{keyword}%"),
        ))

    stmt = select(BankProduct).order_by(BankProduct.base_rate.desc().nullslast()).limit(limit)
    if conditions:
        stmt = stmt.where(and_(*conditions))

    result = await db.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        return "조건에 맞는 은행 수신상품이 없습니다."

    lines = [f"[은행 수신상품 검색 결과 - {len(rows)}건]"]
    for r in rows:
        lines.append(
            f"■ {r.bank_name} | {r.product_name} ({r.product_group})\n"
            f"  기간:{r.min_period}~{r.max_period} | "
            f"기본금리:{r.base_rate}% | 최대금리:{r.max_rate}% | "
            f"예금자보호:{r.deposit_protection} | "
            f"상품유형:{r.deposit_type}"
        )
    return "\n".join(lines)


async def search_funds(db: AsyncSession, args: dict[str, Any]) -> str:
    """공모펀드 검색."""
    conditions = []
    limit = min(int(args.get("limit", 10)), 20)

    if mt := args.get("main_type"):
        conditions.append(FundProduct.main_type.ilike(f"%{mt}%"))
    if rg := args.get("max_risk_grade"):
        conditions.append(FundProduct.risk_grade <= int(rg))
    if mr := args.get("min_return_1y"):
        conditions.append(FundProduct.return_1y >= float(mr))
    if args.get("is_retirement"):
        conditions.append(FundProduct.is_retirement.is_(True))
    if args.get("is_esg"):
        conditions.append(FundProduct.is_esg.is_(True))
    if keyword := args.get("keyword"):
        conditions.append(or_(
            FundProduct.fund_name.ilike(f"%{keyword}%"),
            FundProduct.company_name.ilike(f"%{keyword}%"),
            FundProduct.strategy.ilike(f"%{keyword}%"),
        ))

    stmt = select(FundProduct).order_by(FundProduct.return_1y.desc().nullslast()).limit(limit)
    if conditions:
        stmt = stmt.where(and_(*conditions))

    result = await db.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        return "조건에 맞는 펀드 상품이 없습니다."

    lines = [f"[공모펀드 검색 결과 - {len(rows)}건]"]
    for r in rows:
        retire = "퇴직연금" if r.is_retirement else ""
        esg = "ESG" if r.is_esg else ""
        aum = r.aum or 0
        lines.append(
            f"■ {r.fund_name} ({r.company_name})\n"
            f"  유형:{r.main_type}/{r.mid_type} | "
            f"위험등급:{r.risk_grade} | 1년수익률:{r.return_1y}% | "
            f"운용보수:{r.expense_ratio}% | 순자산:{aum:,.0f}원 {retire}{esg}"
        )
    return "\n".join(lines)
