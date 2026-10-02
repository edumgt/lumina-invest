/* 로그인 후 통합 대시보드. 개별 조회 실패는 해당 지표에만 표시한다. */
import { api, escHtml } from '/js/common.js';
import { GNB_MENUS } from '/js/core.js';

let loading = false;
const metric = (label, value, note = '') => `<div class="overview-metric"><span>${escHtml(label)}</span><strong>${escHtml(value)}</strong><small>${escHtml(note)}</small></div>`;
const number = (value, suffix = '', digits = 1) => value !== null && value !== undefined && Number.isFinite(Number(value)) ? Number(value).toLocaleString('ko-KR', { maximumFractionDigits: digits }) + suffix : '데이터 없음';
function features(key) {
  return GNB_MENUS[key].items.filter(item => item.key !== 'dashboard').map(item => `<a class="overview-feature" href="#${item.key}"><i class="${item.icon}"></i><span>${escHtml(item.label)}</span><i class="fa-solid fa-arrow-right"></i></a>`).join('');
}
async function fill(id, request, render) {
  const el = document.getElementById(id);
  el.innerHTML = '<p class="overview-note" role="status">지표를 불러오는 중입니다…</p>';
  try {
    const data = await request();
    if (data.error) throw new Error(data.error);
    el.innerHTML = render(data);
  } catch {
    el.innerHTML = '<p class="overview-note" role="status">데이터를 불러오지 못했습니다. 새로고침으로 다시 시도하세요.</p>';
  }
}
export async function loadDashboard() {
  if (loading) return;
  loading = true;
  const button = document.getElementById('overview-refresh');
  button.disabled = true;
  document.getElementById('overview-robo-features').innerHTML = features('agent');
  document.getElementById('overview-indicator-features').innerHTML = features('company');
  try {
    await Promise.allSettled([
      fill('overview-robo-metrics', () => api('/api/rebalance/status'), ({ snapshot: s, plan }) =>
        metric('모의계좌 총 자산', number(s.total_asset, '원', 0)) + metric('현금 비중', number(s.cash_weight_pct, '%')) + metric('최대 비중 이탈', number(s.max_drift_pct, '%p')) + metric('리밸런싱 계획', plan.is_active ? '활성' : '비활성', plan.auto_execute ? '자동 체결' : '수동 승인')),
      fill('overview-decision', () => api('/api/quant/auto/status'), data =>
        metric('모의 투자 의사결정', data.running ? '실행 중' : '중지됨') + metric('현재 판단 신호', number(data.signals?.length, '개', 0))),
      fill('overview-indicator-metrics', () => api('/api/quant/pipeline?symbol=005930.KS&period=1y&strategy=rsi&cost_bps=10&slippage_bps=0'), data =>
        metric('누적 수익률', number(data.total_return_pct, '%')) + metric('샤프 비율', number(data.sharpe_ratio, '', 2)) + metric('최대 낙폭 (MDD)', number(data.mdd_pct, '%')) + metric('승률', number(data.win_rate_pct, '%')) + metric('거래 횟수', number(data.trade_count, '회', 0)) + metric('매수 후 보유 수익률', number(data.buy_hold_return_pct, '%'))),
      fill('overview-saved', () => api('/api/custom-indicators'), data => metric('내 커스텀 인디케이터', number(data.items?.length, '개', 0))),
    ]);
    document.getElementById('overview-updated').textContent = '조회 완료 · ' + new Date().toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', hour12: false });
  } finally { loading = false; button.disabled = false; }
}
document.getElementById('overview-refresh').addEventListener('click', loadDashboard);
