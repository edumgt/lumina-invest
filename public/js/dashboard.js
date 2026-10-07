/* 로그인 후 통합 대시보드. 개별 조회 실패는 해당 지표에만 표시한다. */
import { api, escHtml, setToast } from '/js/common.js';
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
  loadKisQuickstart();
  try {
    await Promise.allSettled([
      loadAccountTabs(),
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

// ── 투자 사이트별 현재 투자액 탭 (KIS 모의투자가 가장 왼쪽) ─────────────────
let accountTabs = [];
let activeTab = 'kis';
try { activeTab = localStorage.getItem('overview.accountTab') || 'kis'; } catch {}
const money = (v, cur = 'KRW') => v === null || v === undefined || !Number.isFinite(Number(v)) ? '데이터 없음'
  : cur === 'USD' ? '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 }) : Number(v).toLocaleString('ko-KR', { maximumFractionDigits: 0 }) + '원';
const signed = (v, suffix) => v === null || v === undefined || !Number.isFinite(Number(v)) ? '' : (Number(v) >= 0 ? '+' : '') + Number(v).toLocaleString('ko-KR', { maximumFractionDigits: 2 }) + suffix;
function renderAccountPanel() {
  const panel = document.getElementById('overview-account-panel');
  const tab = accountTabs.find(t => t.key === activeTab) || accountTabs[0];
  if (!panel || !tab) return;
  document.querySelectorAll('#overview-account-tabs .overview-tab').forEach(b => {
    const on = b.dataset.key === tab.key; b.classList.toggle('active', on); b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  if (tab.key === 'robo') { panel.innerHTML = tab.html || '<p class="overview-note">데이터를 불러오지 못했습니다.</p>'; return; }
  if (!tab.connected) {
    panel.innerHTML = `<div class="overview-metric wide"><span>${escHtml(tab.site)}</span><strong>미연동</strong><small>${escHtml(tab.error || tab.note || '')}</small>${tab.link ? `<a class="underline text-xs" href="${tab.link}">설정으로 이동</a>` : ''}</div>`;
    return;
  }
  const pnlNote = signed(tab.pnl_pct, '%') ? `손익 ${signed(tab.pnl, tab.currency === 'USD' ? ' USD' : '원')} (${signed(tab.pnl_pct, '%')})` : (tab.pnl !== null && tab.pnl !== undefined ? `손익 ${signed(tab.pnl, '원')}` : '');
  let html = metric('현재 투자액 (보유 평가)', money(tab.invested, tab.currency), tab.note || '') +
    metric('현금 · 예수금', money(tab.cash, tab.currency)) +
    metric('총 자산', money(tab.total, tab.currency), pnlNote) +
    metric('보유 종목', tab.positions === null || tab.positions === undefined ? '데이터 없음' : number(tab.positions, '개', 0));
  if (tab.key === 'kis') html += metric('자동매매', tab.auto_trade_running ? '실행 중 (live · KIS)' : '꺼짐', `미체결 실주문 ${number(tab.open_live_orders, '건', 0)} · ${escHtml(tab.environment === 'real' ? '실전' : '모의(Testbed)')}`);
  if (tab.key === 'paper' && tab.breakdown) html += metric('자산별 평가', `주식 ${money(tab.breakdown.stocks)}`, `코인 ${money(tab.breakdown.crypto)} · 대체 ${money(tab.breakdown.alternatives)}`);
  panel.innerHTML = html;
}
function renderAccountTabBar() {
  const bar = document.getElementById('overview-account-tabs');
  if (!bar) return;
  bar.innerHTML = accountTabs.map(t => `<button type="button" role="tab" class="overview-tab" data-key="${t.key}" aria-selected="false"><span class="dot ${t.connected ? 'on' : ''}"></span>${escHtml(t.label)}</button>`).join('');
  bar.querySelectorAll('.overview-tab').forEach(b => b.addEventListener('click', () => {
    activeTab = b.dataset.key;
    try { localStorage.setItem('overview.accountTab', activeTab); } catch {}
    renderAccountPanel();
  }));
}
async function loadAccountTabs() {
  const panel = document.getElementById('overview-account-panel');
  if (!panel) return;
  const [acc, robo] = await Promise.allSettled([api('/api/dashboard/accounts'), api('/api/rebalance/status')]);
  accountTabs = acc.status === 'fulfilled' ? (acc.value.tabs || []) : [
    { key: 'kis', label: 'KIS 모의투자', site: '한국투자증권 Testbed', connected: false, error: acc.reason?.message || '불러오지 못했습니다' },
  ];
  const roboTab = { key: 'robo', label: '로보 모의계좌', site: '리밸런싱 엔진', connected: robo.status === 'fulfilled' };
  if (robo.status === 'fulfilled') {
    const { snapshot: s, plan } = robo.value;
    roboTab.html = metric('모의계좌 총 자산', number(s.total_asset, '원', 0)) + metric('현금 비중', number(s.cash_weight_pct, '%')) + metric('최대 비중 이탈', number(s.max_drift_pct, '%p')) + metric('리밸런싱 계획', plan.is_active ? '활성' : '비활성', plan.auto_execute ? '자동 체결' : '수동 승인');
  }
  accountTabs.push(roboTab);
  if (!accountTabs.some(t => t.key === activeTab)) activeTab = 'kis';
  renderAccountTabBar();
  renderAccountPanel();
}

// ── KIS 모의투자 원클릭 ─────────────────────────────────────────────────
const KIS_BLOCK_TEXT = {
  not_connected: 'KIS 연동이 되어 있지 않습니다. 서버의 stock-coin-trade 게이트웨이 또는 Secrets Manager(KIS_SECRETS_NAME) 설정이 필요합니다.',
  real_environment: '현재 KIS 경로가 실전(real)입니다. 원클릭 모의투자는 Testbed 환경에서만 시작합니다.',
  kill_switch: '비상 정지 상태입니다. 자동매매 현황에서 해제 후 시작하세요.',
  batch_exclusive: 'KIS 모의투자 배치가 단독 실행 모드(KIS_PAPER_BATCH_EXCLUSIVE=true)라 사용자 KIS 자동매매는 다음 사이클에 꺼집니다. 배치 결과는 「KIS 모의투자결과」에서 보세요.',
};
let kisIntervalMin = 3;   // 서버 QUANT_CYCLE_SEC(분). quickstart 응답으로 갱신
async function loadKisQuickstart() {
  const btn = document.getElementById('overview-kis-start');
  const el = document.getElementById('overview-kis-status');
  if (!btn || !el) return;
  try {
    const st = await api('/api/quant/kis/quickstart');
    const link = st.connected ? `<span class="badge-buy">연동됨</span> ${escHtml(st.route_detail || '')}` : `<span class="badge-sell">미연동</span> ${escHtml(st.route_detail || '')}`;
    const sb = st.system_batch || {};
    kisIntervalMin = Number(st.interval_min) || 3;
    const aggr = st.aggressive ? ' · <span class="badge-buy">공격 모드</span>' : '';
    // 계정·로그인 무관 백그라운드 배치(KIS_PAPER_BATCH_ENABLED). 켜져 있으면 버튼과 별개로 서버가 매 사이클(기본 3분) 돈다.
    const batchNote = sb.enabled
      ? (sb.running ? ` · <span class="badge-buy">백그라운드 배치 실행 중</span> (서버 설정, 로그인 무관)`
                    : ` · <span class="badge-sell">백그라운드 배치 대기</span>${sb.kill_switch ? ' (비상 정지: ' + escHtml(sb.kill_reason || '') + ')' : ''}`)
      : '';
    if (st.already_started) {
      btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-check"></i> KIS 모의투자 실행 중';
      el.innerHTML = `${link} · 자동매매 <b>실행 중</b> (live · KIS · ${kisIntervalMin}분 주기)${aggr}${batchNote}. <a href="#quant-auto" class="underline">자동매매 현황</a>`;
    } else if (st.ready) {
      btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-play"></i> KIS 모의투자 시작';
      const d = st.defaults || {};
      el.innerHTML = `${link} · 시작하면 AI 추천 ${d.quant_ai_top_n ?? 3}종목 · ${kisIntervalMin}분 주기 · 1회 ${Number(d.quant_per_trade_budget ?? 500000).toLocaleString('ko-KR')}원 · 쿨다운 ${d.risk_cooldown_min ?? 3}분 · 일 주문 ${d.risk_max_orders_per_day ?? 300}건 · 종목 비중 ${d.risk_max_position_pct ?? 20}% 로 자동매매를 켭니다.${aggr}` +
        (st.running ? ` 현재 ${escHtml(st.mode)}/${escHtml(st.broker)} 자동매매가 켜져 있어 설정이 KIS 모의투자로 바뀝니다.` : '') + batchNote;
    } else {
      btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-play"></i> KIS 모의투자 시작';
      el.innerHTML = `${link} · ${escHtml(KIS_BLOCK_TEXT[st.reason] || '지금은 시작할 수 없습니다.')}${batchNote}`;
    }
  } catch (e) {
    btn.disabled = true;
    el.textContent = '연동 상태를 불러오지 못했습니다. ' + (e?.message || '');
  }
}
document.getElementById('overview-kis-start')?.addEventListener('click', async () => {
  const btn = document.getElementById('overview-kis-start');
  const el = document.getElementById('overview-kis-status');
  if (!confirm(`KIS 모의투자(Testbed)를 시작합니다.\nAI 추천 종목으로 ${kisIntervalMin}분마다 시그널을 평가하고, 매수/매도 시 한국투자증권 모의투자 계좌에 실제 주문이 나갑니다.\n\n계속하시겠습니까?`)) return;
  btn.disabled = true; btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 시작 중…';
  try {
    const r = await api('/api/quant/kis/quickstart', { method: 'POST' });
    setToast(r.started ? 'KIS 모의투자 자동매매를 시작했습니다. 첫 사이클을 실행합니다.' : '이미 자동매매가 켜져 있어 설정만 KIS 모의투자로 갱신했습니다.', 'ok');
    if (el) el.innerHTML = `<span class="badge-buy">시작됨</span> ${escHtml(r.route_detail || '')} · 첫 사이클 실행 중, 이후 ${r.interval_min || kisIntervalMin}분마다 반복. <a href="#quant-auto" class="underline">자동매매 현황</a>`;
  } catch (e) {
    setToast(e.message, 'error');
    if (el) el.textContent = '시작 실패: ' + e.message;
  } finally {
    await loadKisQuickstart();
    loadDashboard();
  }
});
