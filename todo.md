# TODO — KIS 자동매매 연동 (lumina-invest 담당분)

> 작성일: 2026-10-02
> 3개 저장소(domain-rag-lab / lumina-invest / stock-coin-trade)를 연결해
> **시그널 → 위험관리 → KIS 실주문 → 체결 확인** 파이프라인을 구축한다.
> 이 파일은 lumina-invest 담당분이다. 같은 이름의 todo.md가 다른 두 저장소에도 있다.

---

## 0. 연동 방식과 실행 흐름

### 0-1. 연동 방식: 사이트 통합이 아닌 **저장소별 API 연동**

- 세 저장소는 **각자 독립 배포·독립 DB**를 유지한다. 코드나 화면을 한 저장소로 합치지 않는다.
- 저장소 간 통신은 **HTTP API만** 사용한다 (파일 공유·DB 직접 접근 없음).
  - domain-rag-lab → lumina-invest : 백테스트 결과/전략 스펙 API
  - lumina-invest → stock-coin-trade : 주문·체결조회·잔고 Open API (API Key 인증)
- 각 저장소는 자기 API의 **계약(요청/응답 스키마)과 버전**에 책임을 진다. 상대 저장소 내부 모듈을 import하지 않는다.

### 0-2. 최초 트리거: lumina-invest 웹앱 **종목 선정 화면**

자동매매는 사용자가 lumina-invest 웹앱에서 종목을 고르고 자동매매를 켜는 순간부터 시작된다.

```
[사용자] lumina-invest 웹앱 (public/app.html, public/js/quant.js)
   │  ① 퀀트 화면에서 종목 선정 + 리스크 한도 입력
   │     POST /api/stocks/quant/settings  →  BrokerSettings.quant_selected_symbols, risk_* 저장
   │  ② 자동매매 ON  (quant_mode: paper | live)
   ▼
[lumina-invest] Celery Beat 10분 주기  quant.auto_trade_cycle
   │  ③ _run_quant_cycle()  — selected_symbols 로드 (없으면 AI 상위 N종목)
   │  ④ 시그널 생성: 기술지표 + LightGBM(ml_models.py)  →  매수/매도/관망
   │  ⑤ risk_guard: kill switch → 일손실 한도 → 일 주문 수 → 종목 비중 → 쿨다운
   ▼
[stock-coin-trade] Open API  (HTTP, API Key)
   │  ⑥ POST /openapi/v1/kis/order-approval  →  60초 1회용 승인 토큰
   │  ⑦ POST /openapi/v1/kis/orders  (승인 토큰 + client_order_id)
   │       kis_request(): OAuth 토큰 서버 캐싱, 레이트리밋, 회당 주문 한도, Secrets Manager 키
   ▼
[KIS Testbed / 실전 API]  ──(주문 체결)──►  [stock-coin-trade DB 감사 로그 + 체결 기록]
   │
   │  ⑧ lumina-invest  quant.confirm_fills (1~2분 주기)  GET /openapi/v1/kis/orders/{order_no}
   ▼
[lumina-invest] 체결 반영 → 사이클 로그 → 웹앱 자동매매 현황 화면 / 알림
```

domain-rag-lab은 이 런타임 흐름의 **앞단(사전 검증)**에 위치한다. 종목 선정 화면에서 선택 가능한 전략은
domain-rag-lab LEAN 백테스트를 통과해 export된 전략 스펙만 노출한다.

### 0-3. 5단계 구조와 담당 저장소

| 단계 | 내용 | 담당 |
|------|------|------|
| 1. 신호 생성 & 검증 | LEAN Docker 백테스트 전략 검증 | domain-rag-lab |
|  | LightGBM / 기술지표 매수·매도 시그널 생성 | **lumina-invest** (이 저장소) |
| 2. 스케줄링 & 리스크 제어 | Celery Beat 10분 주기 `quant.auto_trade_cycle` | **lumina-invest** (이 저장소) |
|  | 위험관리 엔진: 중복주문 쿨다운, 일손실 한도, 비상정지(Kill-Switch) | **lumina-invest** (이 저장소) |
| 3. KIS 통합 주문 게이트웨이 | KIS 공통 게이트웨이 `kis_request()` | stock-coin-trade |
|  | 인증 & 보안: OAuth 토큰 서버 캐싱, AWS Secrets Manager 키 관리 | stock-coin-trade |
|  | 안전 장치: 60초 1회용 승인 토큰, 회당 주문 한도 제어 | stock-coin-trade |
| 4. KIS Testbed / 실전 API | 주문 체결 (환경 플래그로 분리) | stock-coin-trade |
| 5. DB 감사 로그 & 체결 기록 | `_audit_kis_call` 감사 로그 + `kis_orders` 체결 기록 | stock-coin-trade (lumina는 사이클 로그에 미러) |

### 0-4. 전체 작업 순서 (3개 저장소 공통)

- [ ] **Phase 0. 계약 정의** — 3개 저장소가 공유할 API 계약을 먼저 고정
  - [x] 전략 스펙 API (domain-rag-lab → lumina-invest) — 계약서 1절, `/backtests/strategies` 구현·연동 완료
  - [ ] 승인 토큰·주문 요청/응답 스키마 (lumina-invest → stock-coin-trade)
  - [ ] 체결 조회·잔고 응답 스키마 (stock-coin-trade → lumina-invest)
- [ ] **Phase 1. 전략 확정** (domain-rag-lab) — 백테스트 통과 전략을 API로 제공
- [ ] **Phase 2. 실주문 경로 구축** (stock-coin-trade) — 모의(Testbed)부터, 실전은 플래그로 분리
- [ ] **Phase 3. 사이클 연결** (lumina-invest) — 종목 선정 화면 → 시그널 → risk_guard → 승인 토큰 → 주문 → 체결 확인
- [ ] **Phase 4. 모의 통합 테스트** — 종목 선정 화면에서 시작해 KIS Testbed 체결까지 end-to-end 1주 이상 운영
- [ ] **Phase 5. 실전 전환** — 소액·소수 종목부터, kill switch 수동 점검 후 개방

---

## 1. 현재 확인된 상태 (2026-10-02)

- Celery Beat: `app/celery_app.py` `quant-auto-trade-10min` → `quant.auto_trade_cycle` (600초, expires 540)
- 태스크: `app/tasks/sync_tasks.py` `quant_auto_trade_cycle()` → `auto_trade.run_cycle_for_enabled_users()`
- 사이클 본체: `app/services/auto_trade.py`
  - `_run_quant_cycle()`가 `indicators["signal"]`(action/score/reasons)로 매수·매도 판단
  - `_execute_virtual_trade()` — 가상계좌 기록 (항상 수행)
  - `_place_live_order()` — `quant_mode == "live"`일 때 `get_broker_client(broker, key, secret, paper=False).place_order()` 호출
  - `emergency_halt()` — 비상 정지
- 위험관리: `app/services/risk_guard.py`
  - 쿨다운(`acquire_order_slot`/`release_order_slot`), 일 주문 수(`orders_today`/`increment_orders_today`), 종목 비중, 일손실 한도, kill switch
  - Redis 장애 시 메모리 폴백
- 설정 모델: `app/models/trading.py` `BrokerSettings` — `broker`, `app_key`, `app_secret`, `account_no`, `quant_mode`(paper/live), `risk_*` 컬럼
- 자체 KIS 클라이언트: `app/services/brokers/kis.py` `KISClient.place_order()`
  - ⚠️ 지정가(`ORD_DVSN="00"`) 고정, `EXCG_ID_DVSN_CD` 없음
  - ⚠️ `r.raise_for_status()`만 확인 → KIS는 HTTP 200에 `rt_cd != "0"`으로 실패를 돌려주므로 **실패 주문이 성공으로 기록될 수 있음**
  - ⚠️ tr_id가 구버전(`TTTC0802U`/`TTTC0801U`). stock-coin-trade는 신버전(`VTTC0012U`/`VTTC0011U`) 사용 → 통일 필요
  - ⚠️ 주문 접수 후 **체결 확인 로직 없음**
- LEAN 백테스트: `app/services/lean_backtest.py` (domain-rag-lab과 중복 구현)

---

## 2. 이 저장소에서 할 일

### 2-1. 계약 합의 (Phase 0)
- [x] 전략 스펙 → 시그널 매핑 방식: 지표 점수 정규화 + 스펙 임계값 재판정(+ML 가중)으로 결정. entry/exit 규칙의 직접 해석은 미구현(아래 남은 작업)
- [x] stock-coin-trade에 보낼 주문 요청 스키마 합의
  - 필수: `symbol`(6자리), `side`(BUY/SELL), `quantity`, `order_type`(MARKET/LIMIT), `price`, `environment`(paper/real), **`client_order_id`**(멱등키, 중복 방지)
- [x] 체결 조회 응답 스키마 합의 (`order_no`, `status`(접수/부분체결/체결/거부/취소), `filled_qty`, `avg_price`)

### 2-1b. 최초 트리거: 종목 선정 화면 (Phase 3) — 자동매매 시작점
현재: `public/js/quant.js` → `POST /api/stocks/quant/settings` (`app/routes/stocks.py` `save_quant_settings`) → `BrokerSettings.quant_selected_symbols`, `risk_*` 저장 → `_run_quant_cycle()`가 `selected_symbols` 사용. 자동매매 ON/OFF와 kill switch(`/api/quant/risk/kill-switch`) UI도 존재.
- [x] 종목 선정 화면에 **전략 선택 드롭다운** 추가 — domain-rag-lab `GET /backtest/strategies` 결과만 노출 (백테스트 합격 전략만 선택 가능)
- [x] 선택한 `strategy_id`/`version`을 `quant/settings`에 함께 저장 (`BrokerSettings` 컬럼 추가, alembic)
- [x] 화면에 **실행 모드 표시**: paper(Testbed) / live(실전) 구분과 live 전환 시 2단계 확인 모달
- [x] 종목 선정 저장 시 서버 측 검증: 종목코드 6자리, 최대 종목 수, 종목당 비중 합 ≤ 100%
- [x] 저장 직후 "다음 사이클 실행 예정 시각"과 마지막 사이클 결과(`cycle_log`)를 화면에 표시
- [x] 자동매매 현황 화면: 주문 접수 → 체결 확인 상태를 `live_orders` 기준으로 표시 (2-4 연동)

### 2-2. 전략 스펙 로더 + 시그널 엔진 (Phase 3)
- [x] `app/services/strategy_loader.py` 신설 — domain-rag-lab 전략 스펙 **API**(`GET /backtest/strategies/{id}`) 호출, TTL 캐시(Redis) 적용. 파일 공유 방식은 사용하지 않음
- [x] domain-rag-lab 접속 설정: `DOMAIN_RAG_LAB_BASE_URL`, `DOMAIN_RAG_LAB_API_KEY` (env + `app/config`)
- [x] `_run_quant_cycle()`의 시그널 규칙을 하드코딩 대신 스펙 기반으로 평가하도록 교체
- [x] 기술지표 시그널과 **LightGBM 예측**(`app/services/ml_models.py`)을 합산하는 규칙을 스펙 필드로 정의 (가중치, 임계값)
- [x] LightGBM 모델 버전·학습일을 사이클 로그에 기록, 모델 미로드 시 기술지표만으로 폴백
- [x] 사이클 로그(`cycle_log`)에 사용 스펙 id/version 기록

### 2-3. 실주문 경로를 stock-coin-trade Open API로 전환 (Phase 3)
- [x] `app/services/brokers/` 에 `stock_coin_trade_gateway.py`(가칭) 추가
  - 2단계 호출: ① `POST /openapi/v1/kis/order-approval` → 60초 1회용 승인 토큰 ② `POST /openapi/v1/kis/orders` (승인 토큰 + 주문 본문). 둘 다 API Key 헤더 인증
  - 승인 토큰은 주문 의도(symbol/side/qty/price) 해시에 묶이므로 ①과 ② 사이에 수량·가격을 바꾸지 않는다
  - `client_order_id` = `f"{user_id}:{symbol}:{side}:{today}:{cycle_seq}"` 형태로 생성
  - 타임아웃·재시도 정책: 주문은 **재시도 금지**(중복 체결 위험), 승인 토큰 발급·조회만 재시도
- [x] `_place_live_order()`를 게이트웨이 경유로 교체. 기존 `KISClient` 직접 호출은 폴백 또는 삭제 (결정 필요)
- [x] 게이트웨이 설정 추가: `STOCK_COIN_TRADE_BASE_URL`, `STOCK_COIN_TRADE_API_KEY` (env + `app/config`)
- [x] 응답 `status` 검사(게이트웨이가 rt_cd≠0 을 502 로 변환) — 쿨다운 슬롯 반납은 **하지 않기로 결정**(6-1 결정 사항: 가상 포지션 중복 방지)

### 2-4. 체결 확인 루프 (Phase 3)
- [x] 주문 접수 결과(`order_no`, `client_order_id`)를 DB에 저장하는 `live_orders` 테이블 신설 (alembic)
- [x] 새 Celery 태스크 `quant.confirm_fills` (1~2분 주기) — 미확정 주문을 stock-coin-trade 체결 조회 API로 확인
- [x] 체결 확정 시 가상계좌 기록과 실체결가·수량 차이를 보정(또는 괴리 로그)
- [x] 미체결 주문 처리 정책: N분 후 취소 요청 vs 다음 사이클까지 대기 (결정 후 구현)
- [x] 체결/거부/취소 알림 (`notification.notify_order_*` 확장)

### 2-5. 위험관리 보강 (Phase 3·4)
- [x] `risk_guard`에 **실계좌 기준** 일손실 계산 추가 (현재는 가상계좌 평가액 기준)
  - 사이클 시작 시 stock-coin-trade 잔고 API로 실계좌 평가액 스냅샷
- [x] 미체결 주문 수량을 종목 비중 한도 계산에 포함
- [x] 장 운영시간 가드 (평일 09:00~15:30 KST 외 실주문 생략 — `gateway.is_krx_market_open`; 휴장일 캘린더는 미구현)
- [x] kill switch가 켜지면 **미체결 주문 전량 취소** 요청까지 수행하도록 `emergency_halt()` 확장
- [x] Redis 폴백(메모리) 상태에서 live 주문을 낼지 여부 결정 → 기본은 **paper만 허용** 권장

### 2-6. 테스트 (Phase 4)
- [x] 게이트웨이 mock으로 사이클 단위 테스트 (`tests/`): 성공/`rt_cd`실패/타임아웃/중복키 4케이스
- [x] risk_guard 경계 테스트: 쿨다운 만료 직전·직후, 일 주문 수 한도 도달, kill switch on
- [ ] KIS Testbed 계좌로 Celery Beat 실구동 1주 (Phase 4 체크리스트: 체결률, 슬리피지, 에러율 기록)

---

## 3. 다른 저장소와의 인터페이스

- **← domain-rag-lab**: `GET /backtest/strategies*` (전략 스펙 API, 종목 선정 화면과 사이클이 호출)
- **→ stock-coin-trade**: `POST /openapi/v1/kis/order-approval` (승인 토큰), `POST /openapi/v1/kis/orders` (주문), `GET /openapi/v1/kis/orders/{order_no}` (체결 조회), `GET /openapi/v1/kis/balance` (실계좌 잔고)
- **→ domain-rag-lab** (선택): 실체결 로그 export → 백테스트 대비 분석

---

## 4. 미결 사항 (결정 필요)

- [x] 실주문 최종 경로: **stock-coin-trade 경유로 결정**(6-1). 레거시 직접 호출은 게이트웨이 미설정 시 폴백. 원문: 경유 시 네트워크 홉이 하나 늘고, 직접 호출 시 감사로그·레이트리밋을 lumina가 다시 구현해야 함
- [x] 주문 유형 기본값: **지정가(LIMIT, 매수 호가 올림/매도 내림)**. `STOCK_COIN_TRADE_ORDER_TYPE` 로 변경 가능
- [x] 미체결 주문 취소 타이밍: 기본 끔, `STOCK_COIN_TRADE_CANCEL_OPEN_AFTER_MIN` 으로 분 단위 설정(Phase 4 관찰 후 값 결정)
- [ ] LEAN 백테스트 중복 구현 정리 (domain-rag-lab과 협의)

---

## 5. 개발 소요 예상 시간

> 기준: 각 저장소 코드를 아는 개발자, 하루 6시간 실작업, 영업일(d) 단위. KIS Testbed 계좌·AWS 계정은 준비되어 있다고 가정.
> 추정이므로 ±30% 여유를 둔다. 미결 사항(각 파일 4절)이 늦게 결정되면 그만큼 밀린다.

| Phase | 저장소 | 주요 작업 | 공수 |
|-------|--------|-----------|------|
| 0. 계약 정의 | 공통 | 전략 스펙·주문·체결 스키마, 에러 코드 표, 미결 사항 결정 | 2~3d |
| 1. 전략 확정 | domain-rag-lab | 스펙 스키마, 스펙→main.py 생성기, 결과 파서·합격 기준, 전략 조회 API+인증, 테스트 | 5~7d |
| 2. 실주문 경로 | stock-coin-trade | kis_request 환경 분리·tr_id 매핑 (2d), 실주문 서비스+멱등+승인 토큰 (3d), 체결 조회 (1~2d), Open API 엔드포인트+스코프 (2d), Secrets Manager 연동 (1d), 테스트 (2d) | 10~12d |
| 3. 사이클 연결 | lumina-invest | 종목 선정 화면 확장 (2~3d), 전략 로더+LightGBM 합산 (2~3d), 게이트웨이 2단계 호출 (2d), 체결 확인 태스크+live_orders (2~3d), 위험관리 보강 (2~3d), 테스트 (2d) | 12~16d |
| 4. 모의 통합 테스트 | 공통 | Testbed로 종목 선정 → 체결까지 end-to-end, 1주 관찰 + 버그 수정 | 5d 운영 관찰 + 3~5d 수정 |
| 5. 실전 전환 | 공통 | 실전 플래그·스코프 발급, 소액 운영 1주 관찰, kill switch 리허설 | 2~3d + 5d 관찰 |

### 합계

| 인원 구성 | 실작업 공수 | 캘린더 기간 |
|-----------|-------------|-------------|
| 1인이 순차 진행 | 약 **40~50 영업일** | 약 **10~12주** (관찰 기간 2주 포함) |
| 3인이 저장소별 병렬 진행 (Phase 1·2·3 동시) | 합산 공수는 동일 | 약 **6~7주** — 크리티컬 패스는 lumina-invest(Phase 3) → Phase 4 → Phase 5 |

### AI 에이전트(Claude Code) 개발 기준

> 기준: AI 에이전트가 코드 작성·테스트·마이그레이션을 수행하고, 사람은 계약 결정·코드 리뷰·자격증명 투입·실행 승인만 담당.
> 에이전트 실작업은 **세션 시간(h)**, 사람 몫은 영업일(d)로 구분. 코딩 시간은 크게 줄지만 **외부 대기와 관찰 기간은 줄지 않는다.**

| Phase | 에이전트 실작업 | 사람 몫 (결정·리뷰·승인) | 줄지 않는 대기 |
|-------|----------------|--------------------------|----------------|
| 0. 계약 정의 | 스키마·에러 코드 표 초안 1~2h | 미결 사항 결정 + 초안 검토 0.5~1d | — |
| 1. 전략 확정 (domain-rag-lab) | 스펙 스키마, main.py 생성기, 결과 파서, 조회 API, 테스트 4~6h | 리뷰 0.5d | LEAN Docker 백테스트 실행 시간 (전략당 수 분~수십 분) |
| 2. 실주문 경로 (stock-coin-trade) | 환경 분리, 실주문·멱등·승인 토큰, 체결 조회, Open API, Secrets Manager, 테스트 8~12h | 리뷰 0.5~1d, KIS/AWS 자격증명 투입 | Testbed 스모크 테스트는 장 운영시간에만 가능 |
| 3. 사이클 연결 (lumina-invest) | 종목 선정 화면 확장, 전략 로더+LightGBM 합산, 게이트웨이, 체결 확인 태스크, 위험관리 보강, 테스트 10~14h | 리뷰 1d, 화면 UX 확인 | — |
| 4. 모의 통합 테스트 | 발견 이슈 수정 누적 3~6h | 매일 사이클 로그 점검 | **Testbed 관찰 5 영업일** (장 운영시간 기준, 단축 비권장) |
| 5. 실전 전환 | 플래그·스코프·리허설 스크립트 2~3h | 실전 전환 승인, kill switch 리허설 참여 | **KIS 실전 API 승인 대기** + **소액 운영 관찰 5 영업일** |

| 구분 | 합계 |
|------|------|
| 에이전트 실작업 | 약 **28~43시간** (세션 기준 5~7 영업일) |
| 사람 몫 | 약 **3~4 영업일** (결정 1d, 리뷰 2~3d) |
| 줄지 않는 대기 | 관찰 10 영업일 + KIS 실전 승인 대기 |
| **캘린더 기간** | 약 **3.5~4.5주** (사람 기준 10~12주 대비 약 1/3) |

에이전트 기준으로 Phase 1·2·3은 **같은 날 병렬 세션**으로 돌릴 수 있어 코딩 구간은 1주 안에 끝난다.
전체 기간은 Phase 0 결정 속도와 Phase 4·5의 관찰 기간이 결정한다. 관찰을 각 3 영업일로 줄이면 약 3주까지 단축되지만, 쿨다운·일손실 한도가 실제로 작동하는 장면을 충분히 보지 못하므로 권장하지 않는다.

에이전트 작업 시 추가로 드는 비용은 사람 리뷰다. 주문·자금이 걸린 코드이므로 Phase 2·3 산출물은 **사람이 반드시 라인 단위로 리뷰**하는 것을 전제로 위 사람 몫을 잡았다.

### 기간을 좌우하는 변수
- Phase 0에서 API 계약을 확정하지 못하면 Phase 1·2·3이 병렬로 진행되지 못한다. **계약 확정이 최우선**
- lumina-invest는 기존 `KISClient` 직접 호출을 버리고 stock-coin-trade 경유로 바꾸는 작업이라, 자체 호출 유지로 결정하면 Phase 3에서 2~3d 줄어든다 (대신 stock-coin-trade의 감사 로그·승인 토큰 이점을 잃음)
- KIS 실전 API 승인(계좌 소유자 인증, 모의→실전 전환 절차)은 외부 대기 시간이라 Phase 5 시작 2주 전에 미리 신청한다
- Phase 4 관찰 중 장 휴장일이 끼면 그만큼 연장된다

---

## 6. 작업 보고 (AI 에이전트 인수인계용)

> 이 섹션은 **작업을 이어받는 AI 에이전트가 가장 먼저 읽는 부분**이다. 작업을 끝낼 때마다 아래 형식으로 항목을 추가한다.
> 규칙: ① 완료 항목은 2절 체크박스를 `[x]`로 바꾸고 여기엔 파일 경로·검증 방법을 적는다 ② 미완료는 "다음 작업"에 우선순위와 시작 지점(파일:함수)을 적는다
> ③ 가정·결정은 "결정 사항"에 이유와 함께 적는다 ④ 커밋은 사용자가 한다(에이전트는 커밋하지 않음) ⑤ 테스트 실행 명령을 그대로 적어 재현 가능하게 한다.

### 6-1. 2026-10-02 1차 작업 (Phase 0 + Phase 3 핵심 완료, UI 연결 완료)

**완료**
| 항목 | 파일 | 비고 |
|------|------|------|
| API 계약 v0.2 | `docs/contracts/kis-autotrade-api.md` | 세 저장소 동일 사본 |
| 설정 | `app/config.py` `STOCK_COIN_TRADE_BASE_URL/API_KEY/TIMEOUT/KIS_ENVIRONMENT/ORDER_TYPE`, `DOMAIN_RAG_LAB_BASE_URL/API_KEY`, `STRATEGY_SPEC_CACHE_TTL` | 비어 있으면 레거시(KISClient 직접) 폴백 |
| 게이트웨이 클라이언트 | `app/services/brokers/stock_coin_trade_gateway.py` | 2단계 주문(승인→주문), 멱등키 `make_client_order_id`, 호가 보정 `align_price_to_tick`(매수 올림/매도 내림), 주문 POST 무재시도·조회 1회 재시도, `GatewayError(code)` |
| 전략 로더 | `app/services/strategy_loader.py` | domain-rag-lab `/backtests/strategies*` HTTP + 프로세스 TTL 캐시, 실패 시 만료 캐시 폴백 |
| 모델·마이그레이션 | `app/models/trading.py` `LiveOrder`, `BrokerSettings.quant_strategy_id/version` / `alembic/versions/0009_live_orders.py` | **미적용** — DB 없는 환경. 배포 시 `alembic upgrade head` |
| 사이클 연결 | `app/services/auto_trade.py` `_place_live_order`→`_place_live_order_via_gateway`, `confirm_live_fills`, `apply_strategy_spec_to_symbols/_signal` | broker==kis && 게이트웨이 설정 시 경유. 스펙 있으면 유니버스 제한 + 임계값 재판정, `cycle_log.settings.strategy` 기록 |
| Celery | `app/tasks/sync_tasks.py` `quant.confirm_fills`, `app/celery_app.py` beat 120s | |
| 알림 | `app/services/notification.py` `notify_order_filled` | |
| 라우트 | `app/routes/stocks.py` `GET /api/quant/strategies`, `GET /api/quant/live-orders`, settings GET/POST에 `strategy_id/version`, `live_gateway` | 저장 시 domain-rag-lab 에 스펙 존재 검증(422) |
| UI | `public/app.html`(전략 드롭다운, live 경로 안내, 실주문 현황 패널), `public/js/settings.js`(loadStrategies/renderLiveRoute/loadLiveOrders) | 브라우저 미확인(node 없음, 괄호 균형만 점검) |
| 안전장치 | `app/services/auto_trade.py` `cancel_open_live_orders`(emergency_halt 에서 호출), `_place_live_order_via_gateway` 장시간 가드 / `stock_coin_trade_gateway.is_krx_market_open`, 설정 `STOCK_COIN_TRADE_ENFORCE_MARKET_HOURS` | kill switch → 미체결 실주문 취소 요청. 장외 시각엔 live_orders 행도 만들지 않음 |
| 테스트 19개 | `tests/test_stock_coin_trade_gateway.py`(8), `tests/test_strategy_loader.py`(2), `tests/test_strategy_spec_apply.py`(2), `tests/test_live_order_gateway_path.py`(7: 접수 기록·거부→ERROR·연결불가→UNKNOWN·장외 생략·레거시 폴백·confirm_fills 갱신·비상 정지 취소) | |
| README 안내 | `readme.md` 끝 "KIS 자동매매" 절 | todo.md 6절·계약 문서 링크 |

**검증**
```bash
cd /home/ubuntu/lumina-invest && .venv/bin/python -m pytest tests/test_live_order_gateway_path.py tests/test_stock_coin_trade_gateway.py tests/test_strategy_loader.py tests/test_strategy_spec_apply.py tests/test_risk_guard.py tests/test_session_auth.py -q   # 36 passed
.venv/bin/python -m py_compile app/routes/stocks.py app/services/auto_trade.py   # import 는 libgomp 부재로 불가(아래 제약)
```

**결정 사항 (이유)**
- 실주문 최종 경로 = **stock-coin-trade 경유** (구축안대로). `KISClient` 직접 호출은 게이트웨이 미설정 시 폴백으로 남김 → 미결 4절 1번 항목은 "경유"로 결정
- `quant_mode=live` 사용자의 주문이 나가는 KIS 환경은 사용자별이 아닌 **서버 env `STOCK_COIN_TRADE_KIS_ENVIRONMENT`**(기본 paper). 이유: Phase 4 모의 통합 테스트 중 사용자가 실수로 real 을 고르는 경로를 차단
- 게이트웨이 주문 실패 시 **쿨다운 슬롯을 반납하지 않음**. 이유: 가상계좌 체결은 이미 끝났고 반납하면 다음 사이클에 가상 포지션이 중복 → 실패는 `live_orders.status=ERROR/UNKNOWN` + 알림으로 처리 (2-3의 "실패 시 release_order_slot" 항목은 이 결정으로 **폐기**)
- 지표 점수(약 -8~+8)를 8로 나눠 [-1,1]로 정규화한 뒤 스펙 `signal_weights.buy/sell_threshold` 와 비교. LightGBM 가중 합산은 미구현(아래)
- 멱등키 = `{uid12}:{code}:{B|S}:{YYYYMMDDHHmm}` — 10분 사이클 + 쿨다운 전제에서 유일

**다음 작업 (우선순위순)**
1. `alembic upgrade head` 적용 후 실제 Postgres 로 `GET /api/quant/live-orders`, settings 저장 확인 (`app/routes/stocks.py:list_live_orders`)
2. 사이클 통합 테스트: `_run_quant_cycle` 을 가짜 DB·게이트웨이로 1회 돌려 live_orders 행 생성 확인 (현재 순수 함수·클라이언트만 테스트됨). 시작: `tests/test_stock_coin_trade_gateway.py` 의 `FakeServer` 재사용
3. LightGBM 예측을 `apply_strategy_spec_to_signal` 에 `signal_weights.technical/lightgbm` 가중으로 합산 (`app/services/ml_models.py` 출력 연결). 모델 미로드 시 기술지표만
4. `confirm_live_fills` 에서 FILLED 확정 시 가상계좌(QUANT book) 체결가를 실체결가로 보정 또는 괴리 로그 (2-4 미완 항목)
5. 미체결 N분 후 자동 취소 정책 (kill switch 취소는 완료, 시간 기반 취소는 `confirm_live_fills` 에 추가)
6. 실계좌 기준 일손실: 사이클 시작 시 `gateway.get_balance()` 스냅샷을 `risk_guard.day_start_equity` 에 반영
7. KRX 휴장일 캘린더 (`is_krx_market_open` 은 요일·시각만 본다)

**알려진 제약**
- 이 WSL 환경에 `libgomp.so.1` 이 없어 lightgbm 을 import 하는 모듈(`app/routes/stocks.py`, `quant_pipeline`)과 기존 테스트 5개(`test_backtest_costs`, `test_patterns`, `test_ta_utils_lookahead`, `test_xai`, `test_formula`?)는 실행 불가. `sudo apt-get install libgomp1` 후 전체 스위트 재실행 필요
- `.venv` 는 이 세션에서 `uv venv` 로 새로 만든 것(.gitignore 대상)

### 6-2. 2026-10-02 2차 작업 (6-1 "다음 작업" 3·4·5·6·7 처리)

**완료**
| 6-1 번호 | 항목 | 파일 | 비고 |
|------|------|------|------|
| 3 | ML 가중 합산 | `auto_trade.apply_strategy_spec_to_signal(signal, spec, ml_score)`, `auto_trade.ml_scores_by_symbol()`, 설정 `ML_SCORE_SCALE_PCT` | ML 소스 = SageMaker 배치 `quant_ai_scores.get_batch_training_scores()` 의 `pred_ann_return_pct` 를 ±30%로 정규화. 스펙 `signal_weights.lightgbm>0` 일 때만 로드·가중. 점수 없으면 지표만 + 사유 표기. **`ml_models.py` 의 LightGBM 은 종목별 실시간 예측 API가 없어 배치 점수를 대신 사용** |
| 4 | 체결 괴리 로그 | `confirm_live_fills` | FILLED 시 가상 체결가 대비 실체결 슬리피지(%)를 `live_orders.message` 와 summary `slippage_pct` 에 기록. 가상계좌 보정은 하지 않음(아래 결정) |
| 5 | 미체결 자동 취소 | `confirm_live_fills`, 설정 `STOCK_COIN_TRADE_CANCEL_OPEN_AFTER_MIN`(기본 0=끔) | ACCEPTED/PARTIALLY_FILLED 가 N분 경과하면 `gateway.cancel_order` |
| 6 | 실계좌 기준 일손실 | `auto_trade.live_account_daily_loss()`, 사이클 사전 점검에 추가 | live 모드 + 게이트웨이 설정 시 `get_balance().totalEvalAmount` 기준. Redis 키 `quant:day-equity:{uid}:live:{env}:{날짜}`. 조회 실패 시 사이클을 막지 않음 |
| 7 | KRX 휴장일 | `stock_coin_trade_gateway.KRX_HOLIDAYS_2026`, `krx_holidays()`, 설정 `KRX_EXTRA_HOLIDAYS` | 2026 공휴일·대체공휴일·연말 휴장 내장. **KRX 확정 공시와 대조 필요** |
| — | 테스트 6개 추가 (총 42) | `tests/test_strategy_spec_apply.py`(+2), `tests/test_stock_coin_trade_gateway.py`(+1), `tests/test_live_order_gateway_path.py`(+3) | |

**검증**
```bash
cd /home/ubuntu/lumina-invest && .venv/bin/python -m pytest tests/test_live_order_gateway_path.py tests/test_stock_coin_trade_gateway.py tests/test_strategy_loader.py tests/test_strategy_spec_apply.py tests/test_risk_guard.py tests/test_session_auth.py -q   # 42 passed
```

**결정 사항**
- 실체결가로 가상계좌(QUANT book)를 **보정하지 않는다**. 이유: 가상계좌는 대시보드 표시용 장부이고 보정하면 사이클 간 손익 비교가 흔들린다. 괴리는 로그로만 남기고 Phase 4 분석에 사용
- 미체결 자동 취소는 기본 꺼짐. Phase 4 관찰 뒤 값(예: 20분)을 정한다
- 실계좌 일손실도 가상계좌와 같은 `risk_daily_loss_limit_pct` 를 쓴다(별도 한도 컬럼 없음)
- 수정 중 발견한 버그: `confirm_live_fills` 패치 시 체결 알림 호출이 취소 블록 안으로 밀려 들어갔던 것을 테스트가 잡아 되돌렸다. 블록 교체 패치 후에는 들여쓰기 경계를 반드시 확인

**다음 작업**
1. (6-1의 1) `alembic upgrade head` — DB 필요
2. (6-1의 2) `_run_quant_cycle` 통합 테스트 — 가짜 AsyncSession 이 BrokerSettings/Portfolio/QuantVirtualAccount select 를 모두 흉내 내야 해 보류
3. `KRX_HOLIDAYS_2026` 을 KRX 휴장일 공시와 대조, 2027 추가
4. 종목별 실시간 ML 예측이 필요하면 `ml_models.py` 에 `predict_symbol()` 추가 후 `ml_scores_by_symbol` 교체

### 6-3. 2026-10-02 3차 작업 — 환경 구성·재기동 (6-1 "다음 작업" 1 처리)

**실행함**
| 항목 | 내용 |
|------|------|
| .env | `STOCK_COIN_TRADE_BASE_URL=http://crypto-mock-python:8200`(shared-net 내부 주소), `STOCK_COIN_TRADE_API_KEY=<stock-coin-trade api_key_id=2 원문>`, `STOCK_COIN_TRADE_KIS_ENVIRONMENT=paper`, `STOCK_COIN_TRADE_ORDER_TYPE=LIMIT`, `DOMAIN_RAG_LAB_BASE_URL=http://host.docker.internal:80` |
| 이미지·재기동 | `docker compose build app celery-worker celery-beat` → `up -d`. 기동 로그에서 **alembic 0008 → 0009 적용** 확인(live_orders, broker_settings.quant_strategy_id/version) |
| 컨테이너 확인 | `gateway.is_configured()=True, environment=paper, LIMIT`, `strategy_loader.is_configured()=True`. Celery beat 재시작(2분 `quant.confirm_fills` 포함) |
| 게이트웨이 실호출 | lumina 컨테이너 → stock-coin-trade: 잔고 200, 승인 200, 주문 접수 200(ACCEPTED), 취소 200 — stock-coin-trade todo 6-3 참조 |

**아직 안 한 것 (사용자 조작 필요)**
- 종목 선정 화면에서 **투자 모드 live + 증권사 KIS** 저장 → 10분 사이클이 매수/매도 시그널을 내면 `live_orders` 행이 생기고 실주문 현황 패널에 표시된다. paper 모드는 가상계좌만 체결한다
- 테스트를 빨리 보려면 쿨다운·종목 수를 낮추고 1회 투자금을 작게(예: 30만 원) 둔다. 서버 env 가 paper 이므로 live 로 저장해도 주문은 KIS Testbed 로 간다

**주의 — Testbed 체결 확인**
- KIS 모의투자는 일별주문체결조회 건별 목록을 비워 돌려준다. stock-coin-trade 가 보유수량 변화로 추정(`lookup: holdings_inference`)하므로 `confirm_live_fills` 는 그 결과를 그대로 반영한다. 같은 종목에 열린 주문이 2건 이상이면 추정하지 않으니 Testbed 단계에서는 종목당 1건(쿨다운 ≥ 체결 대기 시간)으로 운용

**검증**
```bash
docker logs fin-ai-app --since 10m | grep -E "alembic|0009"
docker exec fin-ai-app python -c "from app.services.brokers import stock_coin_trade_gateway as g; print(g.is_configured(), g.environment())"
```

### 6-4. 2026-10-02 4차 작업 — 남은 개발 항목 (6-2 "다음 작업" 2 + 2절 미완 항목)

**완료**
| 항목 | 파일 | 비고 |
|------|------|------|
| `_run_quant_cycle` 통합 테스트 | `tests/test_quant_cycle_integration.py`(4) | select 대상 엔티티(QuantVirtualAccount/BrokerSettings/Portfolio/LiveOrder)별로 응답하는 가짜 AsyncSession + MockTransport 게이트웨이. live 모드: 매수 시그널→가상 체결 4주→잔고·승인·주문 3호출→live_orders ACCEPTED 기록 / paper 모드: 게이트웨이 미호출 / 미체결 비중 반영 / kill switch 차단 |
| 미체결 실주문 비중 반영 | `auto_trade.open_live_order_exposure()`, 사이클 사전 점검(live) | 열린 매수 실주문의 잔여수량×주문가를 `position_values` 에 더해 `cap_buy_quantity` 가 보게 함. `cycle_log.risk.open_live_exposure` 기록 |
| Redis 폴백 시 실전 차단 | `_place_live_order_via_gateway` | `risk_guard._redis()` 가 None(메모리 폴백)이면 `environment=real` 주문 생략(`reason: risk_store_unavailable`). paper 는 허용 |
| ML 메타 기록 | `auto_trade._ml_meta`, `strategy_log.ml_generated_at/ml_model` | 배치 점수 `generated_at` 을 사이클 로그에 남김 |
| 종목 선정 서버 검증 | `routes/stocks.py save_quant_settings`, `QUANT_MAX_SELECTED_SYMBOLS=10` | 미지원 코드 422, 10개 초과 422, manual 모드 빈 선택 422, 중복 제거 |
| live 전환 확인 모달 | `public/js/settings.js` | paper→live 로 바꿔 저장할 때 `confirm()` 에 주문 경로 표시 |
| risk_guard 경계 테스트 | `tests/test_risk_guard_boundaries.py`(4) | 쿨다운 만료 직전/직후, 일 주문 수 한도, from_row/kill switch, 실전 Redis 폴백 차단 |
| 재배포 | `docker compose build app celery-worker celery-beat && up -d` | 4차 코드 반영 |

**검증**
```bash
cd /home/ubuntu/lumina-invest && .venv/bin/python -m pytest tests/test_quant_cycle_integration.py tests/test_risk_guard_boundaries.py tests/test_live_order_gateway_path.py tests/test_stock_coin_trade_gateway.py tests/test_strategy_loader.py tests/test_strategy_spec_apply.py tests/test_risk_guard.py tests/test_session_auth.py -q   # 50 passed
```

**결정 사항**
- 종목당 비중 합 검증은 하지 않음 — 현재 설정 모델에 종목별 비중 입력이 없다(1회 투자금·비율 방식). 항목은 "종목코드·최대 종목 수" 검증으로 완료 처리
- 통합 테스트는 `_execute_virtual_trade`·`_equity_snapshot` 을 패치한다(가상계좌 DB 쓰기 로직은 기존 테스트 범위). 게이트웨이 경로·위험관리 게이트·로그 구조가 검증 대상

**다음 작업**
1. `KRX_HOLIDAYS_2026` 공시 대조 (6-2의 3)
2. 종목별 실시간 ML 예측 (6-2의 4)
3. 종목 선정 화면에 "다음 사이클 예정 시각·마지막 사이클 결과" 표시 (2-1b 잔여)
4. 교차 검증(Phase 4): domain-rag-lab 에 전략 1개 export 후 드롭다운 선택 → 사이클 로그 `settings.strategy.applied=true` 확인

### 6-5. 2026-10-02 5차 작업 — 남은 개발 항목 전부 처리 (6-4 "다음 작업" 1·2·3·4)

**완료**
| 6-4 번호 | 항목 | 파일 | 비고 |
|------|------|------|------|
| 1 | 2026 휴장일 대조 | `stock_coin_trade_gateway.KRX_HOLIDAYS_2026` | 공개 일정(jangjeon.kr/events/holidays, glasswallet 2026 휴장일 안내) 기준 **17일**로 수정: 제헌절 7/17 추가, 9/28(추석 대체공휴일로 넣었던 날) 제외. domain-rag-lab 시장시간 파일도 동일 반영. KRX 공식 공시와 재대조는 사용자 확인 권장(7절) |
| 2 | 종목별 실시간 ML 점수 | `app/services/ml_symbol_score.py`, `auto_trade.symbol_ml_score()` | 캔들만으로 Ridge 회귀 5일 수익률 예측 → ±5% = ±1 점수. 배치 점수(SageMaker)가 없는 종목에만 보조로 사용, `strategy_log.ml_source="symbol_ridge"`. lightgbm 비의존 |
| 3 | 사이클 상태 패널 | `public/app.html #quant-cycle-status`, `settings.js loadCycleStatus()` | `/api/auto-trade/status` 로 마지막 실행(KST)·다음 예정(마지막+10분)·마지막 결과(체결/생략/전략/비상정지/실계좌 손익) 표시 |
| 4 | 교차 검증 시작 | domain-rag-lab 샘플 전략 `sample_ma_cross_kr v1` 저장 → `GET /backtests/strategies` 노출 → lumina 컨테이너 `strategy_loader.list_strategies()` 에서 조회 성공 | 드롭다운 노출 경로 확인됨. 사이클 적용(`settings.strategy.applied`)은 화면에서 전략 선택·저장 후 확인(사용자 조작) |
| — | 스펙 entry/exit 규칙 직접 평가 | `auto_trade.evaluate_spec_rules()`, `apply_strategy_spec_to_signal(..., indicators=)` | ma_cross(단기/장기 SMA 비교), momentum(N일 고가 돌파/저가 이탈), buy_hold·dca(always/never). 평가 가능하면 규칙이 action 결정(청산 우선), 불가하면 임계값 방식 유지. 2-1·2-2 의 "규칙 매핑 미구현" 해소 |
| — | 테스트 4개 추가 (총 54) | `tests/test_spec_rules_and_ml_symbol.py`, `test_stock_coin_trade_gateway.py`(+1) | |
| — | 재배포 | app·celery-worker·celery-beat | |

**검증**
```bash
cd /home/ubuntu/lumina-invest && .venv/bin/python -m pytest tests/test_spec_rules_and_ml_symbol.py tests/test_quant_cycle_integration.py tests/test_risk_guard_boundaries.py tests/test_live_order_gateway_path.py tests/test_stock_coin_trade_gateway.py tests/test_strategy_loader.py tests/test_strategy_spec_apply.py tests/test_risk_guard.py tests/test_session_auth.py -q   # 54 passed
```

**에이전트가 더 할 수 있는 개발 항목: 없음.** 남은 것은 7절(의사결정)과 운영 단계(Phase 4·5 관찰)다.

---

## 7. 사용자 의사결정 필요 항목 (에이전트가 대신 정할 수 없는 것)

> 2026-10-02 기준. 결정되면 이 표를 갱신하고 관련 "다음 작업"을 6절에 추가한다.

| # | 결정할 것 | 선택지와 영향 | 에이전트 권고 |
|---|-----------|---------------|---------------|
| L1 | Testbed 통합 테스트 시작 시점과 설정값 | 종목 선정 화면에서 live+KIS 저장. 쿨다운(분)·1회 투자금·종목 수를 어떻게 둘지 | 쿨다운 ≥ 30분, 1회 투자금 30만 원, 종목 1~2개로 1주 관찰 |
| L2 | 미체결 자동 취소 분 수 | `STOCK_COIN_TRADE_CANCEL_OPEN_AFTER_MIN` 0(끔)/20/60. 짧으면 체결률↓, 길면 비중 묶임 | 관찰 1주 후 20분으로 시작 |
| L3 | 실계좌 일손실 한도를 가상계좌와 분리할지 | 현재 같은 `risk_daily_loss_limit_pct` 사용. 분리하려면 컬럼·UI 추가 | 당분간 공용 |
| L4 | 전략 action 결정 방식 | 규칙 평가 가능 시 규칙 우선(현재) vs 임계값 점수 우선 vs 둘 다 만족 시만 매수 | 현재 유지, Phase 4 체결 로그로 재평가 |
| L5 | LEAN 백테스트 중복 구현 정리 | `app/services/lean_backtest.py` 삭제·domain-rag-lab 로 일원화 vs 유지 | domain-rag-lab 로 일원화(전략 API 가 그쪽) |
| L6 | 변경분 커밋 시점·브랜치 | 20개 경로 미커밋 | 기능 단위 커밋 권장 |
| L8 | Testbed 기존 보유 종목을 자동매도 대상으로 할지 | 가상 QUANT 장부에 Testbed 보유수량을 시드하면 매도 시그널 시 실매도 발생. 미시드면 자동매매가 산 수량만 매매 | 1주 관찰은 미시드(신규 매수분만), 이후 결정 |
| L7 | libgomp 설치(sudo) | 설치 전까지 lightgbm 의존 테스트 5개 실행 불가 | `sudo apt-get install -y libgomp1` |

### 6-6. 2026-10-02 운영 시작 — 모의투자(Testbed) 자동매매 가동 (사용자 요청 + 7절 권고 수용)

**적용한 결정 (7절)**
| # | 적용 |
|---|------|
| L1 | 계정 `tester@test.com`(broker_settings 보유 2계정 중 선택; 다른 계정 `aaaaa@aaaaa.com` 은 paper/mock 유지) 를 DB 로 설정: `quant_mode=live, broker=kis, symbol_source=manual, selected=[005930.KS, 035720.KS](Testbed 보유 종목 중 QUANT_STOCKS 에 있는 것), 1회 투자금 300,000, 쿨다운 30분, 일 주문 10건, 종목 비중 20%, 일손실 3%, strategy 없음(기본 규칙), quant_auto_enabled=true`. 서버 env 가 paper 이므로 주문은 KIS Testbed 로 간다 |
| L2 | `STOCK_COIN_TRADE_CANCEL_OPEN_AFTER_MIN` 0 유지 → 관찰 1주 후 20분 |
| L5/R1 | LEAN 정본 domain-rag-lab: `app/services/lean_remote.py` 신설, `routes/lean.py` 가 `DOMAIN_RAG_LAB_BASE_URL` 설정 시 `POST /backtests/run` 으로 위임(422 는 검증 오류, 연결 실패 시 로컬 폴백). 로컬 `lean_backtest.py` 는 폴백으로 유지(삭제는 Phase 4 후) |
| R5 | `.env DOMAIN_RAG_LAB_API_KEY` 설정(domain-rag-lab `STRATEGY_API_KEY` 와 동일) |
| L6 | 커밋: 이 세션에서 기능 단위로 수행(아래) |

**가동 기록**
- 15:08 KST 첫 사이클(수동 트리거 `celery call quant.auto_trade_cycle`): 삼성전자 매수 시그널이지만 가상 QUANT 장부가 이전 paper 운용으로 삼성전자 29.6% 보유 → 비중 한도 20% 로 생략. 카카오는 매도 시그널이나 가상 포지션 없음
- 가상 QUANT 장부 초기화(`portfolio book=QUANT` 삭제, 현금 10,000,000) 후 15:10 재실행 → 삼성전자 1주 가상 체결 → 게이트웨이 → **KIS Testbed 주문 0000030540 ACCEPTED (LIMIT 275,000)**. `live_orders` 1행 기록
- 체결 확인(`quant.confirm_fills` 2분)은 아래 "체결 확인 결과" 참고

**주의**
- Testbed 계좌의 기존 보유(KR모터스·POSCO·삼성전자 38주·카카오·펄어비스)는 가상 장부에 없으므로 **매도 시그널로 팔리지 않는다**. 자동매매가 산 수량만 자동매매가 판다(가상 장부 기준). 기존 보유를 자동매도 대상으로 하려면 가상 장부에 동일 수량을 시드해야 함(결정 필요, 7절 L8)
- 테스트 4개 추가 (`tests/test_lean_remote.py`), 총 58 통과. 재배포 완료

**체결 확인 결과**: (아래 갱신)
