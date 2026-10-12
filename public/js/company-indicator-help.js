// 기업 대시보드의 지표 용어 설명. 실제 수치는 데이터 제공자의 결산 기준을 따른다.
const TERMS = {
  '현재가': ['Current Price', '현재 표시된 주당 거래 가격입니다. 제공처의 최근 거래 가격이며 실시간 시세와 차이가 있을 수 있습니다.', '단위: 원'],
  '시가총액': ['Market Capitalization', '상장 주식 전체를 현재 가격으로 평가한 기업의 시장 가치입니다.', '현재 주가 × 발행 주식 수'],
  'EPS': ['Earnings Per Share · 주당순이익', '기업의 순이익을 보통주 한 주 기준으로 환산한 값입니다. 적자이면 음수가 될 수 있으며 실적 기간과 희석 주식 반영 여부에 따라 달라집니다.', '보통주 귀속 순이익 ÷ 가중평균 주식 수'],
  'BPS': ['Book Value Per Share · 주당순자산', '자기자본을 한 주 기준으로 환산한 장부 가치입니다. 기업을 청산했을 때 실제 받을 금액을 보장하지 않습니다.', '보통주 자기자본 ÷ 주식 수'],
  'PER': ['Price-to-Earnings Ratio · 주가수익비율', '주가가 주당순이익의 몇 배인지 보여 줍니다. 낮은 값만으로 저평가를 판단하기 어렵고 업종·성장성·일회성 이익을 함께 살펴야 합니다. 적자일 때 비교 의미가 제한됩니다.', '주가 ÷ EPS · 단위: 배'],
  'PBR': ['Price-to-Book Ratio · 주가순자산비율', '주가가 주당순자산의 몇 배인지 나타냅니다. 1배 미만은 장부 가치보다 낮다는 뜻이며 수익성이나 자산 손실 가능성도 함께 고려합니다.', '주가 ÷ BPS · 단위: 배'],
  'EV/EBITDA': ['Enterprise Value / Earnings Before Interest, Taxes, Depreciation and Amortization', '기업가치(EV)를 이자·법인세·감가상각비 차감 전 이익(EBITDA)으로 나눈 배수입니다. EV는 보통 시가총액에 순차입금 등을 더한 값입니다. EBITDA는 실제 현금흐름과 같지 않습니다.', '기업가치 ÷ EBITDA · 단위: 배'],
  '배당수익률': ['Dividend Yield', '현재 주가 대비 연간 주당배당금의 비율입니다. 과거 배당 또는 예상 배당 기준으로 제공될 수 있으며 향후 배당을 보장하지 않습니다.', '연간 주당배당금 ÷ 주가 × 100 · 단위: %'],
  '주당배당금': ['Dividend Per Share (DPS)', '주식 한 주에 지급하는 배당금입니다. 화면의 값은 제공처의 연간 배당 기준이며 지급 시기나 확정 배당과 다를 수 있습니다.', '배당금 총액 ÷ 배당 대상 주식 수 · 단위: 원'],
  'DPS': ['Dividend Per Share · 주당배당금', '주식 한 주에 지급하는 배당금입니다. 연간 합계와 회차별 배당금을 구분해서 읽어야 합니다.', '배당금 총액 ÷ 배당 대상 주식 수 · 단위: 원'],
  'ROE': ['Return on Equity · 자기자본이익률', '주주가 투입한 자기자본으로 얼마나 순이익을 냈는지 나타냅니다. 차입금 증가나 작은 자기자본 때문에 높아질 수 있습니다.', '순이익 ÷ 평균 자기자본 × 100 · 단위: %'],
  'ROA': ['Return on Assets · 총자산이익률', '자기자본과 부채로 마련한 전체 자산이 얼마나 순이익을 냈는지 나타냅니다. 자산 규모가 다른 업종끼리 단순 비교하면 왜곡될 수 있습니다.', '순이익 ÷ 평균 총자산 × 100 · 단위: %'],
  '영업이익률': ['Operating Profit Margin', '매출 중 본업의 영업이익이 차지하는 비율입니다. 이자 비용·세금 등 영업 외 항목을 반영한 순이익률과 구분합니다.', '영업이익 ÷ 매출액 × 100 · 단위: %'],
  '부채비율(D/E)': ['Debt-to-Equity Ratio · 차입금/자기자본 비율', '이 화면의 D/E는 Yahoo Finance의 차입금 기준 값입니다. 일반적인 부채총계/자기자본 비율과 범위가 다릅니다. 자기자본이 작거나 음수이면 해석에 주의해야 합니다.', '차입금 ÷ 자기자본 × 100 · 단위: %'],
  '매출액': ['Revenue', '상품 판매와 서비스 제공 등 기업의 영업 활동에서 발생한 수익입니다. 비용을 빼기 전 금액이며 이익과 다릅니다.', '분기 실적 표 단위: 억원'],
  '영업이익': ['Operating Income', '매출액에서 매출원가와 판매·관리비 등 영업 비용을 차감한 본업의 이익입니다.', '매출액 − 매출원가 − 판매비 및 관리비 · 단위: 억원'],
  '순이익': ['Net Income', '영업 외 손익과 세금 등을 반영한 최종 이익입니다. 자산 매각 등 일회성 항목에 영향을 받을 수 있습니다.', '세전이익 − 법인세 비용 · 단위: 억원'],
  '총자산': ['Total Assets', '기업이 보유한 현금·재고·설비·채권 등 경제적 자원의 장부 금액 합계입니다.', '부채총계 + 자기자본 · 단위: 억원'],
  '자기자본': ['Shareholders’ Equity', '총자산에서 부채를 차감한 주주 지분의 장부 가치입니다. 시가총액과는 평가 기준이 다릅니다.', '총자산 − 부채총계 · 단위: 억원'],
  '부채총계': ['Total Liabilities', '차입금뿐 아니라 매입채무·충당부채 등 기업이 부담하는 의무의 장부 금액 합계입니다.', '유동부채 + 비유동부채 · 단위: 억원'],
  '현금및현금성자산': ['Cash and Cash Equivalents', '보유 현금과 단기간에 현금으로 바꿀 수 있고 가치 변동 위험이 작은 금융자산입니다. 모든 단기투자를 포함하지는 않습니다.', '최근 연도 재무상태표 기준 · 단위: 억원'],
};
/* ── 섹터별 투자 인디케이터(#company-sector) 전용 설명 ───────────────────────
   같은 이름이라도 집계 방식이 기업 대시보드와 다르다. 이 화면의 수치는 한 기업이 아니라
   **섹터로 묶은 집계값**이고, 점수·판정은 예측이 아니라 유니버스 내 상대 순위다.
   근거: app/services/sector_indicators.py (SCORE_WEIGHTS·_FACTORS·_w_harmonic·_breadth·verdict_rule).
   공통 사전(TERMS)보다 우선 적용된다. ─────────────────────────────────────── */
const SECTOR_TERMS = {
  'PER': ['Price-to-Earnings Ratio · 주가수익비율 (섹터 집계)', '섹터를 하나의 기업으로 합쳤을 때의 주가수익비율입니다. 시가총액 가중 조화평균으로 집계하며, 적자(0 이하)인 종목은 집계에서 제외합니다. 대형주 비중이 크면 그 기업 쪽으로 끌립니다 — 함께 표시되는 「PER 중위」와 같이 보세요.', '시가총액 가중 조화평균 = Σ시가총액 ÷ Σ(시가총액 ÷ 종목 PER) · 단위: 배'],
  'PER 중위': ['Median PER · PER 중위값', '구성종목 PER 을 크기순으로 줄 세웠을 때 가운데 값입니다. 시가총액과 무관하므로 대형주 쏠림이 걸러집니다. 조화평균값과 크게 벌어지면 섹터 안에서 종목별 밸류에이션 격차가 크다는 뜻입니다.', '구성종목 PER 의 중위값 · 단위: 배'],
  'PBR': ['Price-to-Book Ratio · 주가순자산비율 (섹터 집계)', '섹터 전체의 주가순자산비율입니다. PER 과 같은 시가총액 가중 조화평균으로 집계합니다. Yahoo 가 국내 상장사 PBR 을 주지 않는 경우가 많아 결측 종목이 생기며, 몇 종목이 빠졌는지는 카드 하단 출처 줄에 표시됩니다.', '시가총액 가중 조화평균 · 단위: 배'],
  'PSR': ['Price-to-Sales Ratio · 주가매출비율', '시가총액이 연 매출의 몇 배인지 봅니다. 적자 기업도 값이 나오므로 이익이 들쭉날쭉한 섹터의 저평가를 볼 때 PER 을 보완합니다. 매출이익률이 다른 업종끼리 비교하면 왜곡됩니다.', '시가총액 가중 조화평균 · 단위: 배'],
  'EV/EBITDA': ['Enterprise Value / EBITDA (섹터 집계)', '차입금 구조까지 반영한 기업가치가 감가상각 전 영업이익의 몇 배인지 봅니다. 설비투자가 큰 섹터를 PER 보다 공정하게 비교할 수 있습니다. EBITDA 는 실제 현금흐름과 같지 않습니다.', '시가총액 가중 조화평균 · 단위: 배'],
  '배당수익률': ['Dividend Yield (섹터 집계)', '섹터 구성종목의 배당수익률을 시가총액으로 가중해 평균한 값입니다. 과거·예상 배당 기준이 섞일 수 있고 향후 배당을 보장하지 않습니다.', '시가총액 가중 산술평균 · 단위: %'],
  'ROE': ['Return on Equity · 자기자본이익률 (섹터 집계)', '섹터의 자기자본 대비 순이익률입니다. 상대 점수의 「수익성」 팩터에 들어갑니다. 차입을 늘리거나 자기자본이 작아도 높아질 수 있어 부채비율과 함께 보세요.', '시가총액 가중 산술평균 · 단위: %'],
  'ROA': ['Return on Assets · 총자산이익률 (섹터 집계)', '부채까지 포함한 전체 자산이 만들어낸 순이익률입니다. 자산 규모 차이가 큰 업종끼리의 단순 비교는 왜곡될 수 있습니다.', '시가총액 가중 산술평균 · 단위: %'],
  '영업이익률': ['Operating Profit Margin (섹터 집계)', '섹터 매출에서 본업 영업이익이 차지하는 비율입니다. ROE 와 함께 「수익성」 팩터를 만듭니다.', '시가총액 가중 산술평균 · 단위: %'],
  '순이익률': ['Net Profit Margin · 순이익률', '매출에서 세금·영업외손익까지 모두 반영한 최종 이익의 비율입니다. 일회성 손익에 흔들리므로 영업이익률과 같이 보세요.', '시가총액 가중 산술평균 · 단위: %'],
  '매출 성장': ['Revenue Growth (YoY)', '가장 최근 분기 매출을 **전년 같은 분기**와 비교한 증감률입니다. 전분기 대비가 아니므로 계절성 영향은 줄어듭니다. 「성장」 팩터의 절반을 담당합니다.', '(최근 분기 매출 ÷ 전년 동기 매출 − 1) × 100 · 단위: %'],
  '이익 성장': ['Earnings Growth (YoY)', '가장 최근 분기 이익을 전년 같은 분기와 비교한 증감률입니다. 기저가 적자이거나 아주 작으면 수치가 과장될 수 있습니다.', '(최근 분기 이익 ÷ 전년 동기 이익 − 1) × 100 · 단위: %'],
  '부채비율 D/E': ['Debt-to-Equity Ratio · 차입금/자기자본', 'Yahoo 의 **차입금 기준** 값이라 일반적인 부채총계/자기자본 비율보다 작게 나옵니다. 「안정성」 팩터에서 낮을수록 높은 점수를 받습니다.', '차입금 ÷ 자기자본 × 100 · 시가총액 가중 산술평균 · 단위: %'],
  '유동비율': ['Current Ratio · 유동비율', '1년 안에 현금화할 수 있는 자산이 1년 안에 갚아야 할 부채의 몇 배인지 봅니다. 「안정성」 팩터에서 높을수록 높은 점수를 받습니다. 통상 1배 미만이면 단기 지급 부담이 큽니다.', '유동자산 ÷ 유동부채 · 시가총액 가중 산술평균 · 단위: 배'],
  '변동성': ['Annualized Volatility · 연율 변동성', '섹터 지수의 최근 20거래일 일간수익률 표준편차를 1년 기준으로 환산한 값입니다. 수익률의 방향이 아니라 흔들림의 크기입니다.', '20일 일간수익률 표준편차 × √252 × 100 · 단위: %'],
  '52주 위치': ['52-Week Range Position', '섹터 지수가 지난 52주 최저~최고 구간에서 어디에 있는지 0~100 으로 나타냅니다. 0 이면 52주 최저, 100 이면 52주 최고입니다.', '(현재 − 52주 최저) ÷ (52주 최고 − 52주 최저) × 100 · 단위: %'],
  '20일선 위': ['Breadth · 20일 이동평균 위 비중', '섹터 지수 한 줄로는 보이지 않는 **쏠림**을 드러냅니다. 지수는 올랐는데 이 비중이 낮으면 소수 대형주가 끌어올린 상승입니다.', '종가가 20일 단순이동평균 위인 구성종목 수 ÷ 값이 계산된 구성종목 수 × 100 · 단위: %'],
  '1개월 상승': ['Breadth · 1개월 플러스 종목 비중', '최근 1개월 수익률이 플러스인 구성종목의 비중입니다. 상승이 섹터 전반인지 일부 종목인지 가립니다.', '1개월 수익률 > 0 인 종목 수 ÷ 값이 계산된 종목 수 × 100 · 단위: %'],
  '1개월': ['1-Month Return · 1개월 수익률', '시가총액 가중으로 만든 **섹터 지수**의 최근 21거래일 수익률입니다. 구성종목 수익률을 단순 평균한 값이 아니라, 섹터를 하나의 포트폴리오로 본 수익률입니다.', '섹터 지수 21거래일 전 대비 변화율 · 단위: %'],
  '3개월': ['3-Month Return · 3개월 수익률', '섹터 지수의 최근 63거래일 수익률입니다. 「모멘텀」 팩터와 KOSPI 대비 초과수익률 계산에 쓰입니다.', '섹터 지수 63거래일 전 대비 변화율 · 단위: %'],
  '6개월': ['6-Month Return · 6개월 수익률', '섹터 지수의 최근 126거래일 수익률입니다.', '섹터 지수 126거래일 전 대비 변화율 · 단위: %'],
  '12개월': ['12-Month Return · 12개월 수익률', '섹터 지수의 최근 252거래일 수익률입니다. 3개월 수익률과 함께 「모멘텀」 팩터에 들어갑니다.', '섹터 지수 252거래일 전 대비 변화율 · 단위: %'],
  '섹터 지수': ['Cap-Weighted Sector Index · 시총 가중 섹터 지수', '구성종목 일봉을 시가총액으로 가중해 만든 섹터 가격 지수입니다. 이 화면의 모든 가격 지표(기간 수익률·변동성·52주 위치)는 이 지수 시계열에서 계산합니다.', 'Σ(종목 시가총액 × 종가) ÷ Σ시가총액 기준 지수화'],
  'vs KOSPI': ['Excess Return · KOSPI 대비 초과수익률', '섹터 3개월 수익률에서 같은 기간 KOSPI(^KS11) 수익률을 뺀 값입니다. 플러스면 시장보다 더 올랐다는 뜻이고, 「모멘텀」 팩터에 들어갑니다.', '섹터 3개월 수익률 − KOSPI 3개월 수익률 · 단위: %p'],
  '상대점수': ['Relative Score · 섹터 상대 점수', '다섯 팩터를 **유니버스 안에서 백분위 순위**로 바꿔 가중평균한 0~100 점입니다. 가중치는 모멘텀 30 · 수익성 25 · 성장 20 · 밸류에이션 15 · 안정성 10 입니다. 데이터가 없는 팩터는 빼고 남은 가중치로 환산합니다. **수익률 예측이 아니라 같은 화면에 있는 섹터끼리의 상대 순위**입니다.', 'Σ(팩터 백분위 × 가중치) ÷ 반영된 가중치 합 · 0~100'],
  '모멘텀': ['Momentum Factor · 모멘텀 팩터 (가중 30%)', '3개월 수익률 · 12개월 수익률 · KOSPI 대비 3개월 초과수익률, 세 지표의 백분위 순위 평균입니다. 상대 점수에서 가장 큰 비중을 차지합니다.', '세 지표 백분위의 평균 · 가중 30%'],
  '수익성': ['Profitability Factor · 수익성 팩터 (가중 25%)', 'ROE 와 영업이익률의 백분위 순위 평균입니다.', '두 지표 백분위의 평균 · 가중 25%'],
  '성장': ['Growth Factor · 성장 팩터 (가중 20%)', '매출 성장률과 이익 성장률(둘 다 전년 동기 대비)의 백분위 순위 평균입니다.', '두 지표 백분위의 평균 · 가중 20%'],
  '밸류에이션': ['Valuation Factor · 밸류에이션 팩터 (가중 15%)', 'PER · PBR · PSR · EV/EBITDA 의 백분위 순위 평균이며, 이 네 지표는 **낮을수록 높은 점수**를 받습니다. Yahoo 가 국내 PBR 을 자주 비워 두기 때문에 PSR·EV/EBITDA 까지 함께 봅니다.', '네 지표 백분위의 평균(낮을수록 유리) · 가중 15%'],
  '안정성': ['Stability Factor · 재무안정성 팩터 (가중 10%)', '부채비율 D/E(낮을수록 유리)와 유동비율(높을수록 유리)의 백분위 순위 평균입니다.', '두 지표 백분위의 평균 · 가중 10%'],
  '비중확대': ['Overweight · 비중확대', '상대 점수가 60 이상일 때 붙는 라벨입니다. 같은 화면 섹터들 중 상대적으로 낫다는 뜻이며 **매수 추천이나 수익률 예측이 아닙니다**.', '상대 점수 ≥ 60'],
  '중립': ['Neutral · 중립', '상대 점수가 41~59 인 구간입니다.', '41 ≤ 상대 점수 ≤ 59'],
  '비중축소': ['Underweight · 비중축소', '상대 점수가 40 이하일 때 붙는 라벨입니다.', '상대 점수 ≤ 40'],
  '판단 불가': ['Not Rated · 판단 불가', '팩터를 하나도 계산할 수 없어 상대 점수가 비어 있는 상태입니다. 값을 지어내지 않고 판정을 보류합니다.', '상대 점수 = N/A'],
  '벤치마크': ['Benchmark · 비교 기준 지수', '이 화면의 비교 기준은 KOSPI(^KS11)입니다. 섹터 수익률에서 이 지수 수익률을 빼 초과분을 계산합니다.', '^KS11 · 기간별 1·3·6·12개월'],
  'N/A': ['Not Available · 값 없음', '데이터 제공처가 값을 주지 않았다는 표시입니다. 이 화면은 빈 값을 추정치로 채우지 않고 N/A 로 두며, 몇 종목이 빠졌는지 카드 하단 출처 줄에 적습니다.', '결측은 집계·점수에서 제외'],
};

const escape = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const buildPattern = dict => new RegExp(Object.keys(dict).sort((a,b)=>b.length-a.length).map(escape).join('|'), 'g');
/* 화면별 사전. 섹터 화면은 같은 이름이라도 집계가 다르므로 SECTOR_TERMS 가 공통 사전을 덮는다. */
const DICTS = {
  'company-dashboard': TERMS,
  'company-sector': { ...TERMS, ...SECTOR_TERMS },
};
let dialog;
function showHelp(term, dict) {
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.id = 'company-indicator-dialog';
    dialog.setAttribute('aria-labelledby', 'company-indicator-title');
    dialog.style.cssText = 'width:min(520px,calc(100vw - 32px));max-height:85vh;overflow:auto;padding:24px;border:1px solid var(--border);border-radius:16px;background:var(--surf);color:var(--text);';
    dialog.innerHTML = '<form method="dialog" style="display:flex;justify-content:space-between;align-items:center;gap:16px"><h2 id="company-indicator-title" style="font-size:18px;font-weight:700"></h2><button class="btn-secondary" aria-label="지표 설명 닫기">닫기</button></form><p id="company-indicator-full" style="margin:14px 0;color:var(--accent);font-weight:600;overflow-wrap:anywhere"></p><p id="company-indicator-description" style="line-height:1.8"></p><p id="company-indicator-formula" style="margin-top:16px;padding:12px;border-radius:8px;background:var(--bg);font-size:13px"></p>';
    dialog.addEventListener('click', e => { if(e.target === dialog) { const r=dialog.getBoundingClientRect(); if(e.clientX<r.left || e.clientX>r.right || e.clientY<r.top || e.clientY>r.bottom) dialog.close(); } });
    document.body.append(dialog);
    const style=document.createElement('style');
    style.textContent='#company-indicator-dialog::backdrop{background:rgba(0,0,0,.55)} .company-indicator-term{font:inherit;color:inherit;background:none;border:0;border-bottom:1px dotted var(--accent);padding:0;cursor:pointer;text-align:left} .company-indicator-term:focus-visible{outline:2px solid var(--accent);outline-offset:3px}';
    document.head.append(style);
  }
  const [full,description,formula] = (dict || TERMS)[term];
  dialog.querySelector('#company-indicator-title').textContent=term;
  dialog.querySelector('#company-indicator-full').textContent=full;
  dialog.querySelector('#company-indicator-description').textContent=description;
  dialog.querySelector('#company-indicator-formula').textContent=formula;
  dialog.showModal();
}
/** 지표 이름을 찾아 모달을 띄우는 버튼으로 바꾼다. 다시 그린 뒤에도 안전하게 재호출할 수 있다
    (이미 버튼이 된 이름은 closest('button') 검사에서 걸러진다). */
export function attachIndicatorHelp(view = 'company-dashboard') {
  const dict = DICTS[view];
  if (!dict) return;
  const pattern = buildPattern(dict);
  const root=document.querySelector(`.view[data-view="${view}"]`);
  if (!root) return;
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
  const nodes=[];
  while(walker.nextNode()) {
    const node=walker.currentNode;
    if(!node.parentElement.closest('button,script,style') && node.textContent.match(pattern)) nodes.push(node);
  }
  for(const node of nodes) {
    const fragment=document.createDocumentFragment(); let last=0;
    for(const match of node.textContent.matchAll(pattern)) {
      fragment.append(document.createTextNode(node.textContent.slice(last,match.index)));
      const button=document.createElement('button');button.type='button';button.className='company-indicator-term';button.textContent=match[0];button.dataset.indicator=match[0];button.setAttribute('aria-label',match[0]+' 전체 이름과 설명 보기');button.addEventListener('click',()=>showHelp(button.dataset.indicator,dict));fragment.append(button);last=match.index+match[0].length;
    }
    fragment.append(document.createTextNode(node.textContent.slice(last)));node.replaceWith(fragment);
  }
}
