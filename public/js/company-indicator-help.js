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
const escape = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const pattern = new RegExp(Object.keys(TERMS).sort((a,b)=>b.length-a.length).map(escape).join('|'), 'g');
let dialog;
function showHelp(term) {
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
  const [full,description,formula] = TERMS[term];
  dialog.querySelector('#company-indicator-title').textContent=term;
  dialog.querySelector('#company-indicator-full').textContent=full;
  dialog.querySelector('#company-indicator-description').textContent=description;
  dialog.querySelector('#company-indicator-formula').textContent=formula;
  dialog.showModal();
}
export function attachIndicatorHelp() {
  const root=document.querySelector('.view[data-view="company-dashboard"]');
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
      const button=document.createElement('button');button.type='button';button.className='company-indicator-term';button.textContent=match[0];button.dataset.indicator=match[0];button.setAttribute('aria-label',match[0]+' 전체 이름과 설명 보기');button.addEventListener('click',()=>showHelp(match[0]));fragment.append(button);last=match.index+match[0].length;
    }
    fragment.append(document.createTextNode(node.textContent.slice(last)));node.replaceWith(fragment);
  }
}
