import { api, setToast } from '/js/common.js';
let catalogStocks=[];
function fillSelect(select, rows) {
  const previous=select.value;
  select.replaceChildren();
  for(const s of rows){const o=document.createElement('option');o.value=s.symbol;o.textContent=`${s.name} · ${s.market} · ${s.code}`;select.append(o);}
  if(rows.some(s=>s.symbol===previous))select.value=previous;
}
document.getElementById('ind-stock-search')?.addEventListener('input', e=>{
  const q=e.target.value.trim().toLowerCase(), select=document.getElementById('ind-symbol'), previous=select.value;
  fillSelect(select,catalogStocks.filter(s=>s.symbol===previous || (s.name+' '+s.code).toLowerCase().includes(q)));
});
document.getElementById('korea-universe-refresh')?.addEventListener('click', ()=>loadKoreaUniverse());
document.getElementById('korea-universe-scan')?.addEventListener('click',async()=>{
  const button=document.getElementById('korea-universe-scan');button.disabled=true;
  try{await api('/api/stocks/universe/scan?force=true',{method:'POST'});setToast('전종목 데이터 수집을 요청했습니다. 잠시 후 수집 상태를 새로고침하세요. 주문은 전송되지 않습니다.','ok');}
  catch(e){setToast(e.message,'error');}
  finally{button.disabled=false;}
});
export async function loadKoreaUniverse() {
  const status = document.getElementById('korea-universe-status');
  try {
    const [catalog, scan] = await Promise.all([api('/api/stocks/quant/list'),api('/api/stocks/universe/status')]);
    const stocks = catalog.stocks || []; if(!stocks.length)throw Error('종목 목록이 비어 있습니다'); catalogStocks=stocks;
    for(const id of ['ind-symbol']) {
      const select = document.getElementById(id); if(!select) continue;
      fillSelect(select,stocks);
    }
    const c = scan.coverage || {};
    if(status)status.textContent=`코스피 ${catalog.universe?.markets?.KOSPI ?? 0} · 코스닥 ${catalog.universe?.markets?.KOSDAQ ?? 0} · 분석 ${c.ready ?? 0}/${c.total ?? stocks.length} · 미수집 ${c.not_processed ?? 0} · 자료 부족 ${c.partial ?? 0} · 조회 실패 ${c.unavailable ?? 0} · 오래된 자료 ${c.stale ?? 0} · 종목 목록 수집 ${catalog.universe?.updated_at ? new Date(catalog.universe.updated_at).toLocaleString('ko-KR', {timeZone:'Asia/Seoul'}) : '-'} · 배치 갱신 ${scan.updated_at ? new Date(scan.updated_at).toLocaleString('ko-KR', {timeZone:'Asia/Seoul'}) : '대기'}`;
  }catch(e){if(status)status.textContent=`전종목 목록 조회 실패: ${e.message}`;}
}
