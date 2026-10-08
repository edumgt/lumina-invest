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


### 6-28. 2026-10-07 공통 LLM 모델 qwen2.5:7b → 3b 교체 (사용자 요청)

**배경**: fd 호스트는 2 vCPU · 8GB 를 15개 컨테이너가 공유한다. 7b(4.7GB)는 메모리의 절반 이상을 차지하고 콜드 로딩만 30초대였다.

**fd Ollama 실측** (호스트 load 1.27, num_ctx 2048 · num_predict 160, 앱과 같은 조건)

| 모델 | 콜드(로딩 포함) | 웜 | 생성 속도 |
|------|------|------|------|
| qwen2.5:3b | 40.7초 | 16.6초 (33토큰) | 1.99 tok/s |
| qwen2.5:1.5b | 53.2초 | 39.4초 (160토큰) | 4.06 tok/s |

토큰당 속도는 1.5b 가 3b 의 약 2배다. 160토큰 답변 기준 3b ≈ 80초, 1.5b ≈ 39초로 추정된다.

**변경**: `app/config.py` `LLM_MODEL` 기본값 3b, `docker-compose.yml` 의 `COMPOSE_LLM_MODEL` 기본값 4곳·모델 pull 주석, 화면 라벨 「Qwen 7B」→「Qwen」(`public/js/agent.js`, `public/app.html`). 모델 크기는 서버 설정이 정하므로 UI 에 고정 표기하지 않는다.

**주의**: 모델 교체만으로는 체감이 크게 좋아지지 않는다. `num_predict` 를 100 이하로 줄이고 `keep_alive` 를 30분 이상으로 두어 콜드 로딩을 피하는 쪽이 효과가 크다. 벤치마크 시 `ollama run` CLI 는 토큰 상한이 없어 수천 토큰을 생성하며 호스트를 포화시킨다(2026-10-07 실제 발생). HTTP API 에 `num_predict` 를 주고 측정할 것.

**7b 삭제 순서**: 배포 전에 지우면 구 코드가 도는 컨테이너가 깨진다. ① 이 변경 배포 → ② `sudo docker exec fin-ai-ollama ollama rm qwen2.5:7b`(4.7GB 회수).

## 7. 사용자 의사결정 필요 항목 (에이전트가 대신 정할 수 없는 것)

> 2026-10-02 기준. 결정되면 이 표를 갱신하고 관련 "다음 작업"을 6절에 추가한다.

| # | 결정할 것 | 선택지와 영향 | 에이전트 권고 |
|---|-----------|---------------|---------------|
| L1 | Testbed 통합 테스트 시작 시점과 설정값 | 종목 선정 화면에서 live+KIS 저장. 쿨다운(분)·1회 투자금·종목 수를 어떻게 둘지 | 쿨다운 ≥ 30분, 1회 투자금 30만 원, 종목 1~2개로 1주 관찰 |
| L2 | 미체결 자동 취소 분 수 | `STOCK_COIN_TRADE_CANCEL_OPEN_AFTER_MIN` 0(끔)/20/60. 짧으면 체결률↓, 길면 비중 묶임 | 관찰 1주 후 20분으로 시작 |
| L3 | 실계좌 일손실 한도를 가상계좌와 분리할지 | 현재 같은 `risk_daily_loss_limit_pct` 사용. 분리하려면 컬럼·UI 추가 | 당분간 공용 |
| L4 | 전략 action 결정 방식 | 규칙 평가 가능 시 규칙 우선(현재) vs 임계값 점수 우선 vs 둘 다 만족 시만 매수 | 현재 유지, Phase 4 체결 로그로 재평가 |
| L5 | LEAN 백테스트 중복 구현 정리 | `app/services/lean_backtest.py` 삭제·domain-rag-lab 로 일원화 vs 유지 | domain-rag-lab 로 일원화(전략 API 가 그쪽) |
| L6 | ~~변경분 커밋 시점·브랜치~~ **완료**(2026-10-02 푸시, origin/main=9478811). 2026-10-06 6-9 변경분(8경로)은 다시 미커밋 | — | 기능 단위 커밋 |
| L8 | Testbed 기존 보유 종목을 자동매도 대상으로 할지 | 가상 QUANT 장부에 Testbed 보유수량을 시드하면 매도 시그널 시 실매도 발생. 미시드면 자동매매가 산 수량만 매매 | 1주 관찰은 미시드(신규 매수분만), 이후 결정 |
| L9 | 지정가 매수의 체결 프리미엄 | 첫 자동 주문(275,000 지정가)이 직후 현재가 276,000 으로 상승해 미체결. 선택: 현재가 그대로(현재) / +1~2틱 프리미엄 / Testbed 는 MARKET | Testbed 관찰 기간은 +1틱(`align_price_to_tick` 에 옵션 추가), 실전은 관찰 후 결정 |
| L7 | libgomp 설치(sudo) | 설치 전까지 lightgbm 의존 테스트 5개 실행 불가 | `sudo apt-get install -y libgomp1` |
| L10 | KIS 자격증명 Secrets Manager 시크릿 이름·IAM (**진행 중** — 6-8절 사용자 수행 명령 참고) | fd EC2 역할에 `secretsmanager:GetSecretValue` 를 붙이고 `.env KIS_SECRETS_NAME` 기입 vs 당분간 게이트웨이(stock-coin-trade 가 KIS 키 보유)만 사용 | 실주문은 게이트웨이가 정본이므로 `KIS_SECRETS_NAME` 은 가격·잔고 조회용으로 기입. 시크릿 이름 `lumina-invest/prod/kis`, environment=paper |
| L11 | 대시보드 「KIS 모의투자 시작」 원클릭 노출 범위 | 모든 로그인 사용자 vs 운영 계정만. 버튼은 경로가 Testbed(paper)일 때만 활성화되지만 누르면 그 계정의 자동매매가 live+KIS 로 바뀐다 | Phase 4 관찰 기간에는 운영 계정 1개로만 사용 |
| L12 | 포지션 단위 청산 규칙(손절·익절·트레일링) 도입 여부 | 현재 매도는 시그널(지표/전략 exit)에만 의존, 일손실 한도만 존재. 손절 −5%·익절 +10% 같은 규칙을 risk_guard 에 추가하면 Testbed 체결 데이터 해석이 바뀜 | Testbed 1주 관찰 뒤 도입, 값은 관찰 결과로 결정 |
| L14 | (2026-10-06) 백그라운드 배치 종목 | `KIS_PAPER_BATCH_SYMBOLS` 비움(AI 추천 상위 3, 기본) vs 고정 종목 | 1주 관찰은 AI 추천 유지, 체결 로그 보고 고정 여부 결정 |
| L16 | (2026-10-06) 공격 모드 파라미터 | 익절 1.5%/손절 1.0%/사이클 매수 2·매도 3/강제 로테이션 매수 ON/시장가. 6-11 참고 | 1~2일 체결 로그 보고 TP/SL·강제 매수 조정 |
| L17 | (2026-10-06) celery-beat 정지 근본 원인 | 6-13. heartbeat/autoheal 로 자동 복구는 되지만 원인 로그가 없다. 재발 시 beat 로그·RestartCount 수집 | 재발 2회 이상이면 beat 를 worker 내장(`-B`) 또는 RedBeat 스케줄러로 교체 검토 |
| L18 | (2026-10-06) 노출 점검 후속 | 6-14 권고 ①~④ 중 어느 것을 적용할지. docs 비공개는 개발 편의↓, .env 추적 해제는 이력 정리(필요 시 git filter-repo) 동반 | ①②③ 적용, 비번·시크릿 교체 |
| L19 | (2026-10-06) 정합성 불일치 시 자동 조치 범위 | 현재는 검출·알림만. 선택: phantom 가상 체결 자동 되돌리기 / 가상 장부를 KIS 보유로 동기화 / 불일치 N건 이상이면 배치 자동 정지 | 1주 관찰 후 '불일치 3건 이상이면 배치 정지' 부터 도입 |
| L20 | (2026-10-06) 섹터 리밸런싱에서 유니버스 밖 기존 보유(기타 섹터) 처리 | 현재: 목표 0 → 전량 매도 후보. 선택: 종목 목표로 고정 / '기타' 섹터 목표 허용 / 리밸런싱 대상에서 제외 옵션 | 1주 관찰 동안은 제외 옵션 추가 전까지 종목 목표로 고정 권고 |
| L15 | (2026-10-06) 비상 정지 후 배치 재가동 주체 | 사람이 kill switch 해제(현재) vs 다음 영업일 자동 재가동 | 손실 반복 위험으로 수동 유지 |
| L21 | (2026-10-06) st 백엔드 정지 재발 방지 | fd `.env` `KIS_CHART_MINUTES=60`·`KIS_CHART_TIMEOUT=30`(env 만, 즉시) / 장외 분봉 생략 코드 / st 캐시 TTL·큐 상한 / st healthcheck+autoheal — stock-coin-trade todo 6-9 표 | env 2개 + 장외 생략 먼저, st 쪽은 다음 정지 때 |
| L13 | 운영 계정에 LEAN 합격 전략 적용 시점 | pr(domain-rag-lab) 합격 전략 0건. 전략 선택 전까지 매수·매도 모두 기술지표 규칙만 사용(ML·LEAN 미적용) | domain-rag-lab 에서 ma_cross/momentum 백테스트 → export 후 종목 선정 화면에서 선택 |
| L22 | (2026-10-07) 공격 모드 강도 2차 — 1회 50만 원·3분 사이클·쿨다운 3분·일 주문 300건(6-22) | 종목 비중 20% 한도 때문에 Testbed 총자산이 250만 원 미만이면 50만 원이 그대로 집행되지 않고 수량이 깎인다. 선택: 비중 한도 상향(예 30~40%) / 사이클 매수 2→3건 / 익절·손절 폭 조정 | 1~2일 체결 로그(`/api/quant/kis/monitor`) 에서 `[위험관리 생략]`·수량 축소 빈도 보고 비중 한도부터 조정 |

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

**체결 확인 결과**: 15:12 `quant.confirm_fills` 가 게이트웨이 상태를 조회 → `holdings_inference` ACCEPTED(보유 38→38, 미체결). 지정가 275,000 접수 직후 현재가 276,000 으로 상승해 장중 미체결 상태. 15:30 장 마감 시 KIS 가 당일 미체결을 자동 취소하므로 다음 거래일 사이클에서 재시도된다 → 7절 L9(체결 프리미엄) 결정 필요


### 6-7. 2026-10-02 6차 작업 — KIS 자격증명 서버 관리(Secrets Manager) + 대시보드 「KIS 모의투자 시작」 원클릭 (사용자 요청)

**요구**: ① 종목 선정 화면에서 증권사 KIS 를 고르면 App Key/Secret/계좌를 사용자가 입력하지 않고 Secrets Manager 값으로 자동 처리, 화면에는 연동 여부만 표시. ② 통합 대시보드 상단 버튼 한 번으로 AI 추천 기반 KIS 모의투자 자동매매 시작.

**구현**
| 항목 | 내용 |
|------|------|
| `app/services/kis_credentials.py` 신설 | `KIS_SECRETS_NAME` 시크릿(JSON `app_key/app_secret/account_no/environment`)을 boto3 로 조회, 성공 600초·실패 60초 캐시, 실패 시 `KIS_APP_KEY/SECRET/ACCOUNT_NO` env 폴백. `status()` 는 연동 여부·소스·계좌 마스킹(`5012****01`)·환경만 반환(키 원문 없음). 관리 대상은 `MANAGED_BROKERS={"kis"}` |
| `app/config.py` | `KIS_SECRETS_NAME`, `KIS_SECRETS_CACHE_TTL`, `KIS_ENVIRONMENT`, `KIS_APP_KEY/APP_SECRET/ACCOUNT_NO` 추가 (`.env.example` 에 설명) |
| `app/routes/stocks.py` | `_apply_credentials`: broker=kis 면 사용자가 보낸 키·계좌를 **DB 에 저장하지 않고 빈 값으로 초기화**. `_resolve_credentials`: kis 는 Secrets Manager, 그 외는 DB 행. `GET /quant/settings`·`/broker/settings` 응답에 `kis_managed`(연동 상태)·`managed_brokers` 추가, kis 는 `app_key` 마스킹 대신 빈 문자열. `/broker/price·balance·order·test` 가 모두 서버 자격증명 사용 |
| `app/services/auto_trade.py` | 레거시 직접 호출 경로(게이트웨이 미설정)도 kis 는 Secrets Manager 자격증명·환경(paper→Testbed URL) 사용. 미연동이면 `{"status":"skipped","reason":"kis_credentials_not_configured"}` 로 사이클 로그에 남김 |
| `app/services/kis_quickstart.py` 신설 + `GET/POST /api/quant/kis/quickstart` | 원클릭: 경로 판정(게이트웨이 → 없으면 서버 KIS 자격증명) 후 **Testbed(paper) 경로일 때만** 시작. 설정을 `broker=kis, quant_mode=live, symbol_source=ai, ai_top_n=3, 1회 30만 원, 쿨다운 30분, 일 주문 10건, 종목 비중 20%, 일손실 3%`(7절 L1 권고)로 저장하고 `auto_trade.start_auto_trade`(DB 플래그 + 즉시 1회 사이클). 차단: 미연동 / 경로 real / 비상 정지 → 409 |
| `public/app.html` · `public/js/dashboard.js` | 통합 대시보드 상단 「KIS 모의투자 (Testbed)」 카드: 연동 배지(연동됨/미연동 + 경로 설명)·시작 버튼(준비됐을 때만 활성, confirm 후 POST)·실행 중이면 버튼 비활성 + 자동매매 현황 링크 |
| `public/js/settings.js` | 증권사 KIS 선택 시 App Key/Secret/계좌/모의체크 입력 그리드(`#broker-credentials-wrap`) 숨김, `#broker-managed` 박스에 연동 여부·소스(Secrets Manager 이름)·환경·계좌 마스킹·오류 표시. 저장 시 kis 면 키 필드를 비워 보냄. 중복 등록돼 있던 `broker-test` 핸들러 1개 제거 |
| 테스트 | `tests/test_kis_credentials.py`(10) · `tests/test_kis_quickstart.py`(11) · `tests/test_legacy_kis_order_managed.py`(2) · `tests/test_kis_quickstart_http.py`(4, TestClient + 의존성 오버라이드) 추가, `test_live_order_gateway_path.py` 레거시 케이스를 새 동작(kis 미연동 → skipped, 타 증권사 → None)으로 갱신. **총 147 통과** |

**운영 적용 절차 (fd.edumgt.co.kr)**
1. 시크릿 생성: `aws secretsmanager create-secret --name lumina-invest/prod/kis --secret-string '{"app_key":"...","app_secret":"...","account_no":"50123456-01","environment":"paper"}' --region ap-northeast-2`
2. fd EC2 인스턴스 역할에 `secretsmanager:GetSecretValue`(해당 시크릿 ARN) 허용. 컨테이너는 인스턴스 메타데이터로 자격증명을 받으므로 compose 변경 없음
3. `.env` 에 `KIS_SECRETS_NAME=lumina-invest/prod/kis` 기입 후 `docker compose up -d app celery-worker celery-beat`
4. 확인: 증권사 API 설정 화면에서 KIS 선택 → 「🔐 KIS 자격증명 · 서버 관리 **연동됨** · 소스 AWS Secrets Manager · 환경 모의(Testbed) · 계좌 5012****01」. 「연결 테스트」가 삼성전자 현재가를 돌려주면 끝
- 게이트웨이(`STOCK_COIN_TRADE_*`)가 설정돼 있으면 실주문은 종전처럼 stock-coin-trade 경유이고, Secrets Manager 자격증명은 가격·잔고 조회와 게이트웨이 폴백에 쓰인다. 둘 다 없으면 대시보드 버튼은 「미연동」으로 비활성
- 기존 DB `broker_settings.app_key/app_secret` 에 남아 있는 KIS 키는 다음 저장(또는 원클릭) 시 빈 값으로 덮어써진다. 즉시 제거하려면 `UPDATE broker_settings SET app_key='', app_secret='', account_no='' WHERE broker='kis'`

**커밋·배포 (16:xx KST, 사용자 요청)**
- 로컬 커밋 `ba9c240`. `git push origin main` 은 **보호 브랜치 규칙으로 거부**(`Cannot update this protected ref`) → 원격 반영은 PR 또는 보호 규칙 예외 필요. fd 배포는 커밋과 무관하게 로컬 작업 트리 rsync 로 수행
- fd.edumgt.co.kr: rsync 14파일 → `compose up -d --build app celery-worker celery-beat` → 공개 `/api/health` 200, `/api/quant/kis/quickstart` 비로그인 401(라우트 등록 확인), `app.html` 에 `overview-kis-start`·`broker-managed` 포함, alembic 추가 마이그레이션 없음(스키마 변경 없음)
- **Secrets Manager 미연동 상태**: 컨테이너에서 `kis_credentials.is_configured()=False`(`.env` 에 `KIS_SECRETS_NAME` 없음). 인스턴스 역할 `fd-edumgt-ssm-role` 로 `GetSecretValue lumina-invest/prod/kis` 시도 → **AccessDeniedException** → 위 "운영 적용 절차" 1·2·3 이 아직 필요. `STOCK_COIN_TRADE_API_KEY` 도 비어 있어 대시보드 버튼은 현재 「미연동」으로 비활성(의도된 안전 동작)


### 6-8. 2026-10-02 7차 작업 — 사이트 연동(st API 키·KIS 시크릿), 매도 로직 점검, 대시보드 사이트별 투자액 탭 (사용자 요청)

**① 연동 결과**
| 항목 | 결과 |
|------|------|
| st.edumgt.co.kr → lumina API 키 | st MariaDB `api_key` 에 `lumina-autotrade`(member_id=1, scopes `kis:order`, is_active=1) 발급. **원문은 어디에도 출력·저장하지 않고** 해시만 DB, 원문은 fd `.env STOCK_COIN_TRADE_API_KEY` 에 직접 기입(백업 `.env.bak.*`). 발급 키로 `GET https://st.edumgt.co.kr/openapi/v1/kis/balance` → **200** |
| fd 재기동 | app·celery-worker·celery-beat 재기동 → 컨테이너에서 `gateway.is_configured()=True, env=paper, LIMIT`. 공개 `/api/health` 200. **이제 live 모드 실주문은 stock-coin-trade 게이트웨이 → KIS Testbed 로 나간다**(레거시 폴백 종료) |
| pr.edumgt.co.kr | 이미 연동됨: 컨테이너에서 `strategy_loader.is_configured()=True`, 합격 전략 0건(백테스트 통과 전략이 아직 없음) |
| st 서버 `.env` | `KIS_PAPER_*`·`KIS_REAL_*` 환경변수가 **없고** `AWS_REGION` 만 있음 → stock-coin-trade 는 KIS 키를 Secrets Manager 에서 읽는 것으로 보임. 계정 086015456585 ap-northeast-2 에 시크릿 `stock-coin-trade/kis`·`stock-coin-trade/kb`·`stock-coin-trade/alpaca` 존재(이름만 확인, 값은 미열람) |
| `KIS_SECRETS_NAME` | **미완료 — 사용자 수행 필요**(아래). 에이전트의 IAM 정책 부여·fd `.env` 시크릿 참조 기입·시크릿 값/구조 확인은 자동 모드 정책으로 차단됨 |

**사용자 수행 (KIS_SECRETS_NAME 연동 마무리)**
```bash
# 1) fd 인스턴스 역할에 시크릿 읽기 허용 (stock-coin-trade/kis 재사용. 별도 시크릿을 만들려면 lumina-invest/prod/kis 로 생성 후 Resource 에 포함)
aws iam put-role-policy --role-name fd-edumgt-ssm-role --policy-name lumina-kis-secret-read --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["secretsmanager:GetSecretValue","secretsmanager:DescribeSecret"],"Resource":["arn:aws:secretsmanager:ap-northeast-2:086015456585:secret:stock-coin-trade/kis-*","arn:aws:secretsmanager:ap-northeast-2:086015456585:secret:lumina-invest/*"]}]}'
# 2) 시크릿 JSON 키 이름 확인 — lumina 파서가 받는 이름: app_key/appkey/KIS_APP_KEY/appKey, app_secret/…, account_no/account/cano/…, environment/env (paper|real)
aws secretsmanager get-secret-value --secret-id stock-coin-trade/kis --region ap-northeast-2 --query SecretString --output text | python3 -c "import sys,json;print(list(json.load(sys.stdin).keys()))"
#    이름이 다르면(예: paper_app_key) app/services/kis_credentials.py 의 _ALIASES 에 추가하거나 lumina-invest/prod/kis 를 위 형식으로 새로 만든다
# 3) fd .env 에 참조 기입 후 재기동 (시크릿은 60초 네거티브 캐시라 권한 부여 직후 1분 내 반영)
ssh -i lumina-invest/fd.edumgt.co.kr.pem ubuntu@43.201.229.188 "cd /home/ubuntu/lumina-invest && printf '\nKIS_SECRETS_NAME=stock-coin-trade/kis\nKIS_ENVIRONMENT=paper\n' >> .env && sudo env COMPOSE_FILE='docker-compose.yml:compose.fd.yml' docker compose up -d app celery-worker celery-beat"
# 4) 확인: 증권사 API 설정 화면에서 KIS 선택 → 「연동됨 · 소스 AWS Secrets Manager」, 대시보드 KIS 탭이 잔고를 표시
```
- `KIS_SECRETS_NAME` 없이도 **게이트웨이가 켜져 있어 실주문·잔고 조회(대시보드 KIS 탭)는 동작**한다. 시크릿은 게이트웨이 장애 시 폴백과 「연결 테스트」(KIS 직접 시세)용

**② 매도도 AI·LEAN 기반인가 — 점검 결과 (`app/services/auto_trade.py`)**
| 모드 | 매수 | 매도 | 비고 |
|------|------|------|------|
| 기본 규칙(전략 미선택, **현재 Testbed 설정**) | `stock._generate_signal` 기술지표 점수(RSI 과매도 +2, 골든크로스 +3, 단기>중기 +1, BB 하단 +1) ≥ 임계 → 매수/강력 매수 | 같은 함수의 **대칭 규칙**(RSI 과매수 −2, 데드크로스 −3, 단기<중기 −1, BB 상단 −1) ≤ 임계 → 매도/강력 매도 | **ML·LEAN 미사용**. 화면 문구의 "AI 종합 시그널"은 지표 규칙 합산을 뜻함 |
| 전략 선택(domain-rag-lab **LEAN 합격 스펙**) | `apply_strategy_spec_to_signal`: 지표 점수 + LightGBM(SageMaker 배치 `ml_scores_by_symbol`, 없으면 종목별 Ridge `symbol_ml_score`) 가중 합산 → `buy_threshold` 이상 매수. 스펙 `entry` 규칙(ma_cross/momentum) 평가 가능하면 규칙이 우선 | 같은 가중 점수 ≤ `sell_threshold` 매도. 스펙 `exit` 규칙 평가 가능하면 **청산 규칙 우선**(exit=True → 매도) | 매수·매도 **동일 경로·동일 가중치**로 AI(ML)·LEAN 스펙 기반 |
| 실행 | 1회 투자금×buy_ratio 수량, 비중 한도·쿨다운·일 주문 수 게이트 → 가상 체결 → 게이트웨이 실주문 | **가상 QUANT 장부 보유분만** 대상, 수량 = 보유×`sell_ratio`(기본 50%), 쿨다운·일 주문 수 게이트 → 가상 체결 → 게이트웨이 실주문 | 보유가 없으면 매도 시그널은 "보유 수량 없음" 으로 생략(Testbed 기존 보유 미시드 — 7절 L8) |

**부족한 점(결정 필요, 7절 L12·L13)**: 손절·익절·트레일링 스탑 등 **포지션 단위 청산 규칙이 없다**(일손실 한도만 포트폴리오 단위 kill switch). 또 현재 운영 계정은 전략 미선택이라 매수·매도 모두 ML·LEAN 을 쓰지 않는다 — LEAN 합격 전략이 pr 에 아직 0건이므로 domain-rag-lab 에서 전략을 백테스트·export 해야 적용된다

**③ 통합 대시보드 「투자 사이트별 현재 투자액」 탭**
| 항목 | 내용 |
|------|------|
| `app/routes/dashboard.py` 신설, `GET /api/dashboard/accounts` | 탭 순서 고정 `kis → quant → paper → us`. **KIS 모의투자**: 게이트웨이 잔고(`cashBalance`/`totalEvalAmount`/`holdings[].evalAmount`) + 자동매매 실행 여부 + 미체결 실주문 수; 게이트웨이 없으면 서버 관리 자격증명으로 KIS 직접 조회; 둘 다 없으면 미연동. **퀀트 가상계좌**: QUANT 장부 현금+보유 평가(현재가, 실패 시 평균단가). **모의투자 계좌**: `paper_trading.account_snapshot`(주식·코인·대체 분해). **미국주식**: Alpaca Paper `/account`(서버 키 있을 때). 탭 하나가 실패해도 나머지 반환(`connected=False, error`) |
| `public/app.html`·`dashboard.js`·`app.css` | 로보 어드바이저 섹션의 「계좌·운용 지표」를 탭 바(`#overview-account-tabs`, 가장 왼쪽 KIS 모의투자)와 패널로 교체. 5번째 탭 「로보 모의계좌」는 기존 `/api/rebalance/status` 지표 유지. 연동 여부 점(초록) 표시, 선택 탭은 localStorage 기억 |
| 테스트 | `tests/test_dashboard_accounts.py`(5) 추가 — 탭 순서·KIS 첫 탭·게이트웨이/직접 조회/미연동·탭 격리·Alpaca. **총 152 통과** |

---

## 8. 운영 배포 (2026-10-02)

**대상 서버**
| 도메인 | 저장소 | EC2 | 배포 방식 |
|--------|--------|-----|-----------|
| fd.edumgt.co.kr | lumina-invest | 43.201.229.188 (`/home/ubuntu/lumina-invest`, compose `docker-compose.yml:compose.fd.yml`) | GitHub Actions `deploy.yml`(push main) 또는 수동 rsync+compose |
| pr.edumgt.co.kr | domain-rag-lab | 같은 서버 (`/home/ubuntu/domain-rag-lab`, `deploy/pr-edumgt/compose.yml`, Caddy alias `pr-api`) | 수동 rsync+compose. `cd.yml` 을 이 compose 로 고쳤으나 시크릿(EC2_HOST 등)은 구서버 값 → 갱신 필요 |
| st.edumgt.co.kr | stock-coin-trade | 43.202.161.134 (`/opt/stock-coin-trade`, ssl+pg-stock 오버레이) | GitHub Actions `deploy-ec2.yml`(push main) 만. 에이전트는 이 서버 SSH 키 탐색이 보안 정책으로 차단돼 직접 접속하지 않음 |

**에이전트가 수행한 것**
- fd 서버 `lumina-invest/.env` 에 추가: `STOCK_COIN_TRADE_BASE_URL=https://st.edumgt.co.kr`, `STOCK_COIN_TRADE_API_KEY=`(**비어 있음 — st 서버에서 발급 후 기입**), `STOCK_COIN_TRADE_KIS_ENVIRONMENT=paper`, `..._ORDER_TYPE=LIMIT`, `..._ENFORCE_MARKET_HOURS=true`, `..._CANCEL_OPEN_AFTER_MIN=0`, `DOMAIN_RAG_LAB_BASE_URL=http://pr-api:8000`, `DOMAIN_RAG_LAB_API_KEY=<키>`
- fd 서버 `domain-rag-lab/.env.prod` 에 `STRATEGY_API_KEY=<같은 키>` 추가, `data/strategies`·`data/lean-workflows` 생성
- domain-rag-lab · lumina-invest 를 fd 서버에 rsync 후 compose 재빌드 (결과는 아래 "배포 결과")
- stock-coin-trade: `docker-compose.yml` 에서 shared-net 참여를 로컬 전용 `docker-compose.override.yml` 로 분리해 운영 서버 compose(-f 지정)가 외부 네트워크를 요구하지 않게 함

**에이전트가 할 수 없어 사용자가 수행할 것**
1. **GitHub 푸시** (분류기가 "외부 게시"로 차단). 세 저장소 모두 로컬 main 이 origin 보다 앞서 있음:
   ```bash
   for r in domain-rag-lab lumina-invest stock-coin-trade; do (cd /home/ubuntu/$r && git push origin main); done
   ```
   - 푸시하면 lumina `deploy.yml`(fd 재배포, 이미 수동 배포돼 동일 결과)과 stock-coin-trade `deploy-ec2.yml`(**st 서버 실제 배포**)이 자동 실행된다. `gh run watch -R edumgt/stock-coin-trade` 로 확인
   - domain-rag-lab `cd.yml`/`cd-ecr.yml` 은 시크릿이 구서버 기준이라 실패할 수 있음(무해). 고치려면 `gh secret set EC2_HOST --body 43.201.229.188 -R edumgt/domain-rag-lab`, `EC2_USER=ubuntu`, `EC2_APP_DIR=/home/ubuntu/domain-rag-lab`, `EC2_SSH_PRIVATE_KEY < lumina-invest/fd.edumgt.co.kr.pem`
2. **st 서버에서 lumina 전용 API 키 발급** (deploy-ec2 완료 후, 기동 시 `api_key.scopes` 컬럼·KIS 테이블이 자동 생성됨):
   ```bash
   # st 서버에서 (ssh ubuntu@43.202.161.134)
   cd /opt/stock-coin-trade
   RAW="eduapi_live_$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"; HASH=$(printf %s "$RAW" | sha256sum | cut -d' ' -f1)
   sudo docker exec crypto-mock-mariadb sh -c "mariadb -u\"\$MARIADB_USER\" -p\"\$MARIADB_PASSWORD\" \"\$MARIADB_DATABASE\" -e \"INSERT INTO api_key (member_id,label,key_prefix,key_hash,is_active,scopes) VALUES (1,'lumina-autotrade','${RAW:0:16}','$HASH',1,'kis:order'); SELECT api_key_id,label,scopes FROM api_key;\""
   echo "$RAW"   # 이 값을 fd 서버 lumina .env 의 STOCK_COIN_TRADE_API_KEY 에 기입
   ```
   - `.env` 에 `KIS_PAPER_APP_KEY/SECRET/ACCOUNT_NO` 가 있어야 하고, `KIS_REAL_ORDER_ENABLED` 는 비워 둔다(false)
   - 확인: `curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $RAW" https://st.edumgt.co.kr/openapi/v1/kis/balance` → 200
3. **fd 서버 lumina `.env` 에 키 기입 후 app·celery 재기동**:
   ```bash
   ssh -i lumina-invest/fd.edumgt.co.kr.pem ubuntu@43.201.229.188 "cd /home/ubuntu/lumina-invest && sed -i 's|^STOCK_COIN_TRADE_API_KEY=.*|STOCK_COIN_TRADE_API_KEY=<RAW>|' .env && sudo env COMPOSE_FILE='docker-compose.yml:compose.fd.yml' docker compose up -d app celery-worker celery-beat"
   ```
   - 키가 비어 있는 동안 fd 의 live 모드는 레거시 직접 호출로 폴백한다(게이트웨이 미사용). 운영 계정은 아직 paper/mock 이므로 실주문은 나가지 않음
4. 운영에서 자동매매를 켤 계정의 종목 선정 화면 설정(live + KIS)은 Testbed 1주 관찰 결정(7절 L1)에 따라 진행

> **2026-10-06 갱신**: 아래 표 작성 이후 상황이 바뀌었다. 푸시·st 배포·API 키 발급은 완료됐고, domain-rag-lab 시크릿도 갱신됐다. 현재 상태는 8-1 절 참고.

**배포 결과 (15:5x KST)**
| 서버 | 결과 |
|------|------|
| fd.edumgt.co.kr (lumina) | rsync + `compose up -d --build` 완료. 컨테이너 내부 `/api/health` 200, **alembic 0009 (head)** 적용, 공개 `https://fd.edumgt.co.kr/api/health` 200. 게이트웨이는 `STOCK_COIN_TRADE_API_KEY` 가 비어 미설정 상태(레거시 폴백) — st 서버 키 발급 후 기입 |
| pr.edumgt.co.kr (domain-rag-lab) | rsync + `deploy/pr-edumgt/compose.yml up --build -d`. 첫 up 에서 api 가 Created 에 머물러(postgres 재생성 대기) `up -d api` 재실행 → healthy. `/health` 200, `/backtests/strategies` 키 없음 401 / 키 있음 200, lumina 컨테이너에서 `pr-api` 조회 성공(전략 0건). 공개 `https://pr.edumgt.co.kr/health` 200 |
| st.edumgt.co.kr (stock-coin-trade) | **미배포** — 사용자 푸시 → `deploy-ec2.yml` 자동 배포 필요 (위 1·2·3 절차) |


### 6-9. 2026-10-06 KIS 모의투자 — 계정·로그인 무관 백그라운드 배치 (사용자 요청)

**요구**: KIS 모의투자 자동매매를 특정 사용자 계정·로그인·대시보드 버튼에 의존하지 않고 서버 배치로 돌린다.

**현황(변경 전)**: celery-beat `quant.auto_trade_cycle`(10분)은 `BrokerSettings.quant_auto_enabled=true` 인 **사용자 계정 행**만 순회했다. 운영은 tester@test.com 행이 켜져 있어야 돌았고, 그 행이 꺼지면(비상 정지·사용자 조작) 멈췄다.

| 변경 | 내용 |
|------|------|
| `app/config.py` | `KIS_PAPER_BATCH_ENABLED`(기본 false), `KIS_PAPER_BATCH_SYMBOLS`(비우면 AI 추천), `KIS_PAPER_BATCH_AI_TOP_N=3`, `KIS_PAPER_BATCH_PER_TRADE_BUDGET=300000` |
| `app/services/kis_batch.py` 신설 | `ensure_system_batch(db)`: env 가 true 고 실주문 경로가 **paper** 면 시스템 사용자(`SYSTEM_USER_ID`, user_id `quant_system`) 행을 `TESTBED_DEFAULTS`(kis·live·AI 3종목·30만 원·비중 20%·일 10건·쿨다운 30분·일손실 3%)로 만들고 `quant_auto_enabled=True`. 멱등. 기존 행은 broker/mode/enabled 만 보장하고 한도·종목은 덮어쓰지 않음. real 경로·미연동이면 켜지 않고 켜져 있던 배치 행은 끈다. `risk_kill_switch` 면 재가동 안 함. env 를 false 로 돌리면 다음 사이클에 배치 행(kis·live)만 끈다 |
| `app/services/auto_trade.py` `run_cycle_for_enabled_users` | 사이클 시작 시 `kis_batch.ensure_system_batch` 호출 → enabled 행 조회(시스템 행 포함) → 순차 실행. 배치 점검 예외는 로그만 남기고 사용자 사이클은 계속. 반환에 `kis_batch` 상태 추가 |
| `app/services/kis_quickstart.py` `readiness` | 응답에 `system_batch`(enabled/running/kill_switch/kill_reason/symbol_source/user_id) 추가 |
| `public/js/dashboard.js` | KIS 카드에 「백그라운드 배치 실행 중 / 대기」 배지 표시(버튼과 별개로 서버가 돈다는 안내) |
| `.env.example` | 위 4개 변수 설명 |
| `tests/test_kis_batch.py` 신설 (15건) | env off 무동작·배치 행만 해제·레거시 행 보존, 신규 생성+시작, 수동 종목, 기존 행 비덮어쓰기, 멱등, kis/live 강제, kill switch 차단, real 차단+해제, 미연동 차단, status/readiness, beat 진입점이 배치 점검 후 시스템 행 실행·점검 실패 시 생존 |

**검증**: `.venv/bin/python -m pytest -q` 전체 167 passed(기존 152 + 신규 15). lightgbm 의존 모듈은 이 환경에서 import 불가라 기존처럼 제외.

**동작 요약**: 사용자 계정 행과 시스템 행은 **각각 독립 사이클**이다. tester 계정이 켜져 있으면 둘 다 돌아 같은 Testbed 계좌에 주문이 두 배로 나갈 수 있다 → 배치로 전환할 때 tester 행의 자동매매를 끄는 것을 권장(아래 운영 적용 3). 가상 QUANT 장부·live_orders·risk_guard 카운터는 user_id 별이므로 시스템 행은 `00000000-0000-0000-0000-000000000001` 로 따로 쌓인다.

**운영 적용 (사용자 수행 — 에이전트는 서버 SSH 가 정책상 차단)**
1. 커밋·푸시 → `deploy.yml` 이 fd 에 자동 배포(오늘 수동 실행으로 전 단계 성공 확인됨).
2. fd 서버 `.env` 에 추가 후 재기동:
   ```bash
   ssh -i lumina-invest/fd.edumgt.co.kr.pem ubuntu@43.201.229.188 "cd /home/ubuntu/lumina-invest && printf '\nKIS_PAPER_BATCH_ENABLED=true\n' >> .env && sudo env COMPOSE_FILE='docker-compose.yml:compose.fd.yml' docker compose up -d app celery-worker celery-beat"
   ```
   (`deploy.yml` 은 `.env` 를 덮어쓰지 않으므로 이후 배포에도 유지된다)
3. 중복 주문 방지: 종목 선정 화면에서 tester@test.com 자동매매 OFF, 또는 DB `UPDATE broker_settings SET quant_auto_enabled=false WHERE user_id='<tester uid>'`.
4. 확인(다음 10분 사이클 후): celery-worker 로그에 `KIS 모의투자 배치 ON — route=stock-coin-trade env=paper created=True`, 대시보드 KIS 카드에 「백그라운드 배치 실행 중」, `SELECT quant_auto_enabled, broker, quant_mode FROM broker_settings WHERE user_id='00000000-0000-0000-0000-000000000001'`.
5. 끄기: `.env` 에서 `KIS_PAPER_BATCH_ENABLED=false` 후 재기동 → 다음 사이클에 시스템 행 해제. 즉시 멈추려면 대시보드 비상 정지.

**결정 필요(7절 추가)**: L14 — 배치 종목을 AI 추천(기본)으로 둘지 `KIS_PAPER_BATCH_SYMBOLS` 로 고정할지. L15 — 비상 정지 후 재가동을 사람이 하는 현재 정책 유지 여부(자동 재가동은 손실 반복 위험으로 넣지 않았다).

### 8-1. 2026-10-06 현황 갱신 (GitHub Actions 직접 배포 정비 + 배치 기능 추가)

**git / 배포 파이프라인**
| 항목 | 상태 |
|------|------|
| origin/main | `9478811` (feat(dashboard): 투자 사이트별 현재 투자액 탭). 10-06 09:50 fetch/pull 결과 로컬=원격 |
| 로컬 미커밋 | 6-9 절 8경로(`app/config.py`, `app/services/kis_batch.py`(신규), `auto_trade.py`, `kis_quickstart.py`, `public/js/dashboard.js`, `.env.example`, `tests/test_kis_batch.py`(신규), `todo.md`) |
| GitHub secret/var | `FUND_WEB_SSH_KEY` ← `fd.edumgt.co.kr.pem` 으로 **갱신**(이전 값은 Permission denied). vars `FUND_WEB_HOST=43.201.229.188`, `FUND_WEB_USER=ubuntu`, `FUND_WEB_COMPOSE_FILE=docker-compose.yml:compose.fd.yml`, `FUND_WEB_DOMAIN=fd.edumgt.co.kr` |
| `deploy.yml` | 수동 실행 run 37396581320 **전 단계 성공**(pytest → SSH → rsync → `compose up -d --build app celery-worker celery-beat` → 내부 헬스 → 공개 도메인). fin-ai-app·celery-beat·celery-worker·ingest 재생성, Up. 이후 push main 마다 자동 |
| 서버 상태 | `https://fd.edumgt.co.kr/api/health` 200. 컨테이너 11개 Up(postgres·redis·neo4j·ollama healthy) |

**자동매매(KIS Testbed)**
| 항목 | 상태 |
|------|------|
| 사용자 계정 경로 | tester@test.com 행 live+kis+quant_auto_enabled(6-6), 게이트웨이 키 발급·`.env` 기입 완료(6-8) → 실주문은 st 게이트웨이 → KIS Testbed. 첫 주문 0000030540 ACCEPTED(미체결) |
| 배치 경로(6-9, **미배포**) | `KIS_PAPER_BATCH_ENABLED` 로 시스템 사용자 행을 켜는 코드 완료·테스트 167 통과. fd `.env` 에 변수 없음 → 푸시·배포·`.env` 기입 후 활성화(6-9 "운영 적용") |
| 실시간 가동 확인 | **미확인** — 에이전트의 fd SSH/DB 조회가 자동 모드 정책(Production Reads)으로 차단. 공개 엔드포인트는 health 만 노출. 확인은 사용자가 celery-worker 로그 또는 `live_orders`/`broker_settings` 조회 |
| `KIS_SECRETS_NAME` | 여전히 미기입(L10). 게이트웨이가 켜져 있어 실주문·잔고 조회는 동작 |

**LEAN(4 저장소 공통 점검 결과)**: 모두 `quantconnect/lean:latest` 를 각자 Docker-outside-of-Docker 로 실행(공용 LEAN 서비스 아님). lumina 는 `lean_backtest.py`+`lean_remote.py`, domain-rag-lab/stock-kms-portal 은 `lean_backtest_service.py`(포크, 25줄 차이), stock-coin-trade 는 `docker/lean/Dockerfile`. 태그가 latest 라 서버별 버전이 다를 수 있음 → L5/R1(일원화)·태그 고정 결정과 함께 처리.

**다음 작업(사용자)**: 6-9 "운영 적용" 1~5. 특히 배치 전환 시 tester 행 OFF(중복 주문 방지).

### 6-10. 2026-10-06 유니버스 3섹터 31종목 조정 + 자동매매 5분 주기 (사용자 요청)

| 변경 | 내용 |
|------|------|
| `app/services/stock.py` `QUANT_STOCKS` | 20종목(자동차·배터리·바이오·금융 포함) → **반도체 12 · IT 10 · K뷰티 9 = 31종목**. `QUANT_SECTORS` 상수 추가. 코스닥은 `.KQ`(리노공업·이오테크닉스·HPSP·원익IPS·주성·솔브레인·더존비즈온·카카오게임즈·펄어비스·실리콘투·클리오·브이티·코스메카코리아). 31종목 모두 Yahoo 차트 API 로 시세 조회 확인(2026-10-06). 제외: 현대차·기아·LG화학·삼성SDI·LG엔솔·S-Oil·삼성바이오·셀트리온·KB·신한·삼성전기·넷마블 |
| `app/celery_app.py` | `quant-auto-trade-10min`(600s) → `quant-auto-trade-5min`(300s, expires 270) |
| `app/tasks/sync_tasks.py` | `quant.auto_trade_cycle` time_limit 540 → **280**(주기 안에 종료, 겹침 방지) |
| `auto_trade.py`·`kis_quickstart.py`·`kis_batch.py`·`routes/stocks.py`·`notification.py`·`models/trading.py`·`gateway` 주석 | `_INTERVAL_SEC=300`, `interval_min=5`, 문구 "10분"→"5분" |
| `public/js/dashboard.js`·`core.js` | 안내 문구 5분 |
| `tests/test_universe_and_schedule.py` 신설(6) | 3섹터·28~35종목·섹터당 ≥8, 심볼 유일·`\d{6}.(KS|KQ)`·게이트웨이 6자리 변환, 핵심 종목 포함, beat 300s/expires<300/`_INTERVAL_SEC`, time_limit<300, quickstart interval 5 |

**검증**: pytest 173 passed. `grep 10분` 잔여는 fx 캐시·변경 이력 주석만.

**영향·주의**
- 운영 계정(tester)의 `quant_selected_symbols=[005930.KS, 035720.KS]` 는 모두 새 유니버스에 있어 그대로 동작. 기존 가상 QUANT 장부에 제외 종목 보유가 있다면 `stock_map` 에 없어 매도 시그널 평가 대상에서 빠진다(현재 삼성전자 1주만 있어 영향 없음).
- 5분 주기여도 **쿨다운 30분·일 주문 10건** 한도는 그대로라 같은 종목 재주문은 30분에 1회. 주문 빈도를 올리려면 종목 선정 화면에서 쿨다운·일 주문 수 조정(L1 재결정).
- 5분 사이클이 31종목 지표를 계산하므로 캔들 캐시(6h) 미스 시 첫 사이클이 길어질 수 있음 → time_limit 280 초과 시 해당 사이클만 중단되고 다음 주기에 재시도. 운영 로그에서 `자동매매 사이클 실패`·`TimeLimitExceeded` 확인 권장.
- 체결 확인 `quant.confirm_fills` 는 2분 그대로.
- 배포는 push → `deploy.yml` 자동. celery-beat 재기동으로 새 스케줄 적용.

### 6-11. 2026-10-06 공격 모드 — 5분봉 시그널로 매 사이클 매수·매도 (사용자 요청 "카카오 이후 거래 없음, 공격적으로")

**원인 진단**: 기본 사이클은 **일봉** 지표(`get_quant_indicators`, 캔들 캐시 6h)라 하루 종일 같은 시그널 → 5분 주기여도 새 주문이 없음. 매도는 가상 QUANT 장부 보유분만 대상(카카오 보유 0 → 생략), 쿨다운 30분, 운영 계정 종목 2개. 삼성전자 1주 주문 뒤 거래가 멈춘 이유.

| 변경 | 내용 |
|------|------|
| `app/services/aggressive_mode.py` 신설 | `get_intraday_indicators`: Yahoo **5분봉**(range 5d, 캐시 4분) → 점수 = 추세(MA5 vs MA20 ±1, 교차 ±2) + 30분 모멘텀(±1, ≥1% ±2) + RSI 보조(±1) + 거래량 급증(+1). `plan()`: 보유분 **익절 +1.5% / 손절 −1.0% 전량 매도**, 약세 시그널(score ≤ −1) sell_ratio 매도(사이클 최대 3건); 대상 종목 (score, 모멘텀) 정렬 → score ≥ 1 상위 최대 2건 매수, **매수 시그널이 없으면 모멘텀 1위 로테이션 매수**. `apply_limits`: 쿨다운 5분·일 주문 200건으로 덮어씀(비중·일손실·kill switch 유지). `order_type()`: 실주문 **MARKET** |
| `app/services/auto_trade.py` | `QUANT_AGGRESSIVE_MODE` 면 지표 소스·한도·대상 pool(≥5)·행동을 plan 으로 교체. 공격 모드에선 plan 이 고른 종목만 거래(나머지 관망). 보유 종목은 대상 밖이어도 매도 점검. `_live_order_type()` 로 LiveOrder 기록·게이트웨이 주문 유형 통일. 사이클 로그 `aggressive{buy,sell,ranked,notes}` |
| `app/services/stock.py` | `get_candles(..., max_age_hours=6)` 파라미터 추가(분봉은 4분 캐시) |
| `app/config.py`·`.env.example` | `QUANT_AGGRESSIVE_*` 12개(기본 OFF) + `KIS_PAPER_BATCH_EXCLUSIVE`(기본 true) |
| `app/services/kis_batch.py` | **배치 단독 실행**: 배치가 켜지면 다른 사용자 계정의 kis·live 자동매매 행을 끈다(같은 Testbed 계좌 중복 주문 방지, audit `quant.kis_batch.exclusive`). paper/mock 행은 유지 |
| `compose.fd.yml` | app·celery-worker·celery-beat 에 `QUANT_AGGRESSIVE_MODE=${…:-true}`, `KIS_PAPER_BATCH_ENABLED=${…:-true}` → **fd 서버는 배포만으로 공격 모드 + 배치 ON**. 끄려면 서버 `.env` 에 `=false` 기입(compose 치환이 .env 를 읽는다) |
| `app/routes/health.py` | `/api/health` 에 `quant{cycle_sec, aggressive_mode, aggressive_interval, kis_paper_batch, kis_environment}` 노출(비민감) — 배포 후 외부에서 활성화 확인용 |
| 테스트 | `tests/test_aggressive_mode.py` 17건(점수·분봉 조회·한도·주문유형·plan 6건·사이클 통합 4건: 전부 관망이어도 모멘텀 1위 MARKET 매수, 익절 전량 매도(대상 밖 종목), 사이클 매수 한도, OFF 시 무영향), `test_kis_batch.py` +2(단독 실행 on/off). 전체 **192 passed** |

**동작 요약(fd, 5분마다)**: 배치 점검(시스템 행 ON, tester 행 kis·live OFF) → 31종목 5분봉 지표 → 보유분 익절/손절/약세 매도(최대 3) → 모멘텀 상위 매수(최대 2, 없으면 1위 강제) → 가상 체결 → 게이트웨이 **시장가** 실주문(장중만) → 2분 후 confirm_fills.

**안전장치 유지**: 종목 비중 20%, 일손실 3%(가상·실계좌 각각) 초과 시 비상 정지, kill switch 수동 해제, real 경로면 배치 자체가 켜지지 않음. 일 주문 200건은 5분×2건 상한(장중 78사이클×≤5건)보다 작게 둔 값.

**주의**
- 시장가 체결은 호가 스프레드만큼 불리할 수 있다(Testbed 라 손익 무관하나 지표 해석 시 참고).
- 로테이션 강제 매수는 시그널 없이도 매수한다 → 하락장에선 손절(−1%)이 자주 걸릴 수 있음. 끄려면 `QUANT_AGGRESSIVE_FORCE_BUY=false`.
- Testbed 기존 보유(KR모터스·POSCO·삼성전자 38주 등)는 가상 장부에 없어 여전히 매도 대상이 아니다(L8).
- tester 계정의 kis·live 자동매매는 배치 단독 실행으로 **자동 OFF** 된다. 계정 기준으로 돌리고 싶으면 `.env KIS_PAPER_BATCH_EXCLUSIVE=false`.

**검증(배포 후)**: `curl https://fd.edumgt.co.kr/api/health` → `quant.aggressive_mode=true, kis_paper_batch=true`. 거래 발생은 다음 5분 사이클부터(장중 09:00~15:30 KST). 서버 로그 `KIS 모의투자 배치 ON`, `공격 모드 매수`, `live_orders` 증가로 확인.

### 6-12. 2026-10-06 배치 거래가 화면에 보이지 않던 문제 + 사이클 견고성 (사용자 "여전히 거래 이력이 없음")

**진단**: 공격 모드·배치는 02:26Z 배포로 활성(`/api/health` quant 블록 확인). 그러나 대시보드 「자동매매 현황」·「실주문 현황」·KIS 탭은 **로그인 사용자 기준** 조회라, 배치 단독 실행으로 tester 행이 꺼진 뒤에는 시스템 사용자(`00000000-…-0001`)로 쌓이는 사이클·주문이 화면에 전혀 보이지 않았다. 서버 로그/DB 는 에이전트가 볼 수 없어(정책) 실제 주문 발생 여부는 아래 "확인" 으로 사용자가 봐야 한다. 요구 재확인: **로그인 사용자가 없어도 백엔드 배치(celery-beat)가 KIS 모의투자를 진행**한다 — 화면은 확인용.

| 변경 | 내용 |
|------|------|
| `routes/stocks.py` `/quant/auto/status` | 배치 실행 중이면 시스템 사용자 사이클을 합쳐 반환(`[배치] ` 접두). 공격 모드 notes(`[공격 모드] …`), 위험관리 생략 사유(`[위험관리 생략] …`), 실주문 상태(`· 실주문 submitted/skipped(market_closed)`)도 로그로 노출. 응답에 `me_running`, `batch` 추가, 시간순 정렬 |
| `routes/stocks.py` `/quant/live-orders` | 배치 실행 중이면 시스템 사용자 주문 포함, 각 행 `owner: me|batch` |
| `routes/dashboard.py` KIS 탭 | `auto_trade_running = 내 행 or 배치`, `batch_running`·`me_running` 추가, 미체결 수에 배치 주문 포함 |
| `services/kis_batch.py` `system_status` | 조회 실패·가짜 행·타 사용자 행에 방어적(getattr, user_id 검사) — 보조 정보가 본 응답을 막지 않게 |
| `services/auto_trade.py` `run_cycle_for_enabled_users` | 배치 점검 예외 시 `db.rollback()` — 실패한 트랜잭션이 남으면 뒤의 select 가 PendingRollbackError 로 **모든 사용자 사이클을 막는** 결함 보강 |
| 테스트 | `tests/test_auto_status_batch_merge.py` 3건(합산·배치 OFF·live-orders owner). 전체 **195 passed** |

**시도했으나 철회**: 로그인 없이 보는 공개 진단 엔드포인트(`/api/health/quant`, 사이클·주문 로그 노출)는 거래 내역 외부 노출이라 추가하지 않음. 상태 확인은 로그인 화면(위 라우트) 또는 서버 로그/DB 로.

**확인(사용자)**: 배포 후 대시보드 로그인 → 「자동매매 현황」에 `[배치]` 항목이 5분마다 늘어나는지. 항목이 있는데 실주문이 `skipped(market_closed)` 면 장외, `[위험관리 생략]` 이면 한도, 시그널만 있고 거래 0 이면 `aggressive` 계획 결과(가격 없음=분봉 미수신). `[배치]` 항목 자체가 없으면 celery 로그의 `Traceback` 확인:
```bash
ssh -i lumina-invest/fd.edumgt.co.kr.pem ubuntu@43.201.229.188 "sudo docker logs --since 30m fin-ai-celery-worker 2>&1 | grep -E 'auto_trade_cycle|Traceback|Error' | tail -30"
```

### 6-13. 2026-10-06 celery-beat 정지 사고 → heartbeat healthcheck + autoheal (사용자 서버 진단 결과 대응)

**서버 진단(사용자 실행, 03:0x UTC)**: 워커 env `QUANT_AGGRESSIVE_MODE=true`, `KIS_PAPER_BATCH_ENABLED=true` 정상. DB `broker_settings`: 시스템 사용자 행 **kis/live/true**(배치 ON), tester 행 kis/live/**false**(단독 실행으로 자동 OFF), 다른 계정 mock/paper kill switch. 즉 배치 코드는 첫 사이클에서 정상 동작. 그러나 **최근 30분 beat 로그에 `auto_trade_cycle` 전송 0건**, 워커에 수신 로그 없음 → beat 가 첫 사이클 뒤 due task 를 보내지 않고 조용히 멈춘 상태였다(원인 로그 미확보). 사용자가 `docker restart fin-ai-celery-beat` 로 복구.

| 변경 | 내용 |
|------|------|
| `app/tasks/beat_health.py` 신설 | `beat.heartbeat` 가 Redis `celery:beat:heartbeat` 에 UTC 시각 기록. `python -m app.tasks.beat_health` = healthcheck CLI(키 나이 ≤180s 정상, 기동 240s 유예, Redis 실패 시 비정상) |
| `app/tasks/sync_tasks.py`·`app/celery_app.py` | `beat.heartbeat` 태스크, beat 스케줄 60s(expires 50) |
| `docker-compose.yml` | celery-beat·celery-worker 에 healthcheck(60s×3) + 라벨 `autoheal=true`. beat 가 안 보내거나 worker 가 안 받으면 둘 다 unhealthy |
| `compose.fd.yml` | `autoheal`(willfarrell/autoheal) 서비스: 라벨 붙은 unhealthy 컨테이너를 30s 간격으로 재시작(기동 300s 유예). docker.sock 마운트 필요 |
| 테스트 | `tests/test_beat_health.py` 4건. 전체 **199 passed** |

**배포 영향**: `deploy.yml` 이 `compose up -d --build --remove-orphans`(전체) 라 autoheal 이 함께 생성된다. 첫 4~5분은 start_period 라 재시작하지 않음.

**남은 미확인**: beat 가 멈춘 근본 원인(로그 미확보). 재발 시 `sudo docker logs --since 2h fin-ai-celery-beat | tail -60` 과 `docker inspect -f '{{.RestartCount}} {{.State.Health.Status}}' fin-ai-celery-beat` 를 7절 L17 로 기록 요청.

**확인(사용자, fd 서버 안에서)**:
```bash
sudo docker logs --since 10m fin-ai-celery-beat 2>&1 | grep -c 'Sending due task'          # 1분당 1(heartbeat)+5분당 1(cycle) 이상
sudo docker logs --since 10m fin-ai-celery-worker 2>&1 | grep -E 'auto_trade_cycle|배치|공격 모드' | tail -10
sudo docker exec fin-ai-redis redis-cli get celery:beat:heartbeat                              # 최근 UTC 시각
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'celery|autoheal'                        # (healthy)
sudo docker exec fin-ai-postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select created_at, symbol, side, order_type, quantity, price, status from live_orders order by created_at desc limit 10;"'
```

### 6-14. 2026-10-06 계좌·키 노출 점검 (사용자 요청)

| 항목 | 결과 |
|------|------|
| 공개 URL `.env`/`.env.prod`/`.git/config` (fd·pr) | 404 — 정적 노출 없음. st 는 모든 경로가 SPA index.html 로 폴백(파일 노출 아님) |
| 인증 필요 API 무인증 호출 (settings·broker·dashboard·live-orders·auto/status·quickstart, pr strategies, st kis) | 전부 401 |
| **OpenAPI 문서 공개** (`/docs`, `/openapi.json`) fd·pr·st(`/api/docs`) | 200 — 라우트·스키마(필드명 app_key 등) 전체 공개. 키 값은 없음. 정보 노출 축소 권고: 운영에서 `docs_url=None, openapi_url=None` |
| **git 추적 .env (4 repo 모두 PUBLIC)** | domain-rag-lab `.env`(개발용: 로컬 postgres 비번 7자 등), lumina `.env.prod`(SESSION_SECRET=change-me 계열 14자, DATABASE_URL 기본 fin_user 비번), `.env.dev`(알림 키 전부 빈 값). **KIS 앱키·계좌·게이트웨이 API 키·pem 은 어느 repo 에도 커밋되지 않음**. pem 은 .gitignore(`*.pem`) |
| compose 하드코딩 | lumina `docker-compose.yml` 에 `POSTGRES_PASSWORD=fin_pass`·`DATABASE_URL` 평문(공개 repo). fd 외부 포트(5432/6379/6333/7474/7687/11434) 모두 closed 라 직접 접속은 불가하나, 공개 비번이므로 교체 권고 |
| 프런트 정적 자산(public/, frontend/) | 키 패턴 0건 |
| 서버 `.env`(fd lumina: STOCK_COIN_TRADE_API_KEY·DOMAIN_RAG_LAB_API_KEY 원문) | 서버에만 존재, deploy rsync 가 `.env*` 제외. 파일 권한·`docker exec env` 노출 범위는 서버에서 `ls -l .env`(600 권고) 로 확인 필요(에이전트 SSH 불가) |
| GitHub Actions 로그 | 시크릿은 `***` 마스킹. 비민감 env 만 출력 |
| 코드 내 상수 | stock-coin-trade `market_bots.py` `BOT_PASSWORD="system-bot-account"`(내부 봇 계정 비번 하드코딩) — 교체·env 이동 권고 |

**권고(우선순위)**: ① 운영 FastAPI docs 비공개(3 서비스) ② 공개 repo 의 `.env`/`.env.prod` 추적 해제(`git rm --cached`) + 서버 DB 비번·SESSION_SECRET 교체(이미 공개 이력이라 삭제만으로는 부족) ③ fd `.env` 권한 600 확인 ④ `BOT_PASSWORD` env 이동. 결정은 7절 L18.

### 6-15. 2026-10-06 첫 공격 모드 주문 UNKNOWN (브이티 018290.KQ 시장가 23주, 02:51Z) → 재전송·타임아웃 보강

**사실**: 배치가 실제로 주문을 냈다(`live_orders` 02:51:31Z BUY MARKET 23 UNKNOWN). UNKNOWN = lumina 가 게이트웨이 응답을 못 받음(`GATEWAY_UNREACHABLE`: 연결 실패 또는 **15초 타임아웃**). st 는 KIS 토큰 발급+주문을 순차 호출해 15초를 넘길 수 있다. 이후 `confirm_fills` 는 당일 주문 목록 매칭으로만 해소하려 했는데 KIS 모의투자는 목록을 비워 주므로 **영구 UNKNOWN** → 비중 점유(open exposure)가 풀리지 않음.

| 변경 | 내용 |
|------|------|
| `config` | `STOCK_COIN_TRADE_ORDER_TIMEOUT=45`(승인·주문 호출 전용), `STOCK_COIN_TRADE_UNKNOWN_RESUBMIT_MIN=10` |
| `gateway` | `_client/_call` 에 호출별 timeout. `place_order` 는 45초 |
| `confirm_live_fills` | UNKNOWN 이고 `client_order_id` 있으면 **같은 intent 를 멱등 재전송**(계약서: 같은 clientOrderId → 새 주문 없이 저장된 order, duplicate=true). st 가 원 주문을 기록했으면 orderNo·상태를 받아 확정, 닿지 않았으면 이번에 접수(최대 1회). 장외면 대기. 생성 후 10분 지나면 `LOST` 로 종료(열린 상태 아님 → 비중 해제) + 오류 알림 |
| 테스트 | +4(재전송 duplicate 확정, 창 초과 LOST, 장외 대기, 주문 호출 45초) → **203 passed** |

**02:51 주문의 실제 결과 확인(사용자)**: 배포 후 다음 confirm_fills(2분) 가 재전송을 시도하지만 이미 10분이 지나 **LOST** 로 종료된다. 실제 체결 여부는 KIS 앱 또는 st 서버 `kis_autotrade_order` 에서 `client_order_id` 로 확인. 체결됐다면 Testbed 계좌에 브이티 23주가 있고, lumina 가상 장부에도 23주가 있으므로 익절/손절 매도 대상이 된다(가상·실계좌 수량 일치). 체결 안 됐다면 가상 장부만 23주 → 매도 시그널 시 실매도가 KIS 에서 잔고 부족으로 거부될 수 있음(st 가 거부 → ERROR 기록, 사이클은 계속).

### 6-16. 2026-10-06 시세 KIS 연계 + 로그·실거래 정합성 점검 모듈 (사용자 요청 2건)

**서버 진단(사용자, 03:1x UTC)**: 시스템 행 ON·kill switch OFF. 사이클 02:32/02:37/02:42/02:51/03:02 — 간격 불규칙은 잦은 배포로 beat 가 재시작된 탓. 매 사이클 시그널 5·거래 2(가상 체결)인데 **live_orders 는 2건만**(02:43 원익IPS FILLED, 02:51 브이티 UNKNOWN) → 가상 체결 뒤 실주문 단계가 대부분 비어 있음. trades 의 `live_order` 상세(D 쿼리)는 미확인 — 아래 정합성 모듈이 이 유형(phantom_trade)을 자동 검출한다.

**① 시세 KIS 연계** — `app/services/kis_market_data.py` 신설
| 항목 | 내용 |
|------|------|
| 소스 | st `GET /api/kis-chart/minutes?symbol=&count<=240&time=HHMMSS`(1분봉, 실시간 — 12:12 KST 조회 시 12:12 봉까지 확인), `GET /api/kis-chart/candles?period=D&count<=300`(일봉), `summary.price`(현재가). 공개 엔드포인트지만 API 키 헤더 동봉 |
| 분봉 | 1분봉 240개 → 5분봉 48개로 집계(`aggregate`). `time` 은 KST 현재시각을 정규장(090000~153000)으로 클램프. 장 전엔 st 가 전 영업일 봉을 오늘 날짜로 라벨링하므로 장중만 신뢰(장외는 주문 자체가 생략됨) |
| 적용 | `aggressive_mode.get_intraday_indicators`: KIS 우선, 실패·빈 응답이면 Yahoo 폴백(`MARKET_DATA_FALLBACK_YAHOO`). `current_price` 는 KIS 현재가. `stock.get_candles`(국내·일봉)·`stock.get_quote`(국내)도 KIS 우선 + Yahoo 폴백, 캐시 키 `candles:kis:…` 분리 |
| 설정 | `MARKET_DATA_SOURCE=kis`(기본), `MARKET_DATA_FALLBACK_YAHOO`, `KIS_CHART_TIMEOUT`, `KIS_CHART_MINUTES` |
| 유지 | 펀더멘털·지수·환율·해외 종목은 Yahoo 그대로 |

**② 정합성 점검 모듈** — `app/services/reconciliation.py` 신설, beat `quant.reconcile`(10분)
| 점검 | 의미 |
|------|------|
| `kis_position_mismatch` | KIS 실제 보유 != 기준선 + 봇 실주문 체결 순수량 → 봇이 모르는 체결·취소·수동 거래 |
| `virtual_vs_live_mismatch` | 가상 장부 != 봇 체결 순수량 → 가상만 체결(phantom) 또는 미추적 체결 |
| `phantom_trade` | 사이클 로그에 가상 filled 인데 `live_order` 없음/skipped/error |
| `stale_open_order` | 열린 실주문이 30분 초과 |
| `unresolved_order` | 당일 LOST/ERROR/UNKNOWN |
| `slippage` | 가상 체결가 대비 실체결가 괴리 > 1% |
- **기준선**: 첫 점검 때 "현재 KIS 보유 − 봇 체결 순수량" 을 `quant:reconcile:baseline:<uid>:<env>`(data_cache)에 저장. 수동 거래 뒤 재설정은 그 키 삭제. 결과는 `quant:reconcile:latest:<uid>`, `/api/quant/auto/status` 의 `reconcile` + 로그 `[정합성] …` 로 화면 표시. 불일치 집합이 바뀔 때만 알림(dispatch) 1회.
- 테스트: `test_kis_market_data.py` 12건, `test_reconciliation.py` 7건, 기존 공격모드 테스트는 Yahoo 경로 고정. 전체 **222 passed**. 코드 커밋 8ed5f3c, 문서는 이 커밋.

**예상 효과**: "가상 2건/실주문 <=1건" 패턴은 다음 점검에서 `phantom_trade`·`virtual_vs_live_mismatch` 로 집계되어 화면·알림에 뜬다. 원인(실주문 함수 미호출 vs 게이트웨이 거부)은 그 `live_status`/`live_reason` 로 갈린다.

### 6-17. 2026-10-06 「KIS 모의투자결과」 전용 모니터링 화면 (사용자 요청)

**질문 답**: KIS 모의투자만 보는 화면은 없었다(통합 대시보드 KIS 탭·자동매매 현황·실주문 현황이 흩어져 있고 모두 로그인 사용자 기준). 로보 어드바이저 메뉴 **맨 아래**에 「KIS 모의투자결과」(`#kis-monitor`)를 신설.

| 변경 | 내용 |
|------|------|
| `app/routes/kis_monitor.py` `GET /api/quant/kis/monitor` (인증) | 한 번에: 배치/공격모드/heartbeat/시세소스/유니버스, KIS 계좌(현금·평가·보유, **★봇 관리 종목** 표시·봇 수량), 봇 실주문(시스템+내 계정, 최근 50·당일 상태 분포·체결률·매수/매도 금액·평균 슬리피지·미해소 UNKNOWN/LOST/ERROR·**평균단가법 실현손익**), 가상 장부, 사이클 이력(대상·매수/매도 계획·거래별 실주문 결과·생략·비상정지·가상 평가), 마지막 beat 실행, 정합성 결과 |
| `public/js/core.js` | `GNB_MENUS.agent` 마지막에 `{key:"kis-monitor", label:"KIS 모의투자결과"}`, 사용법 가이드(`VIEW_GUIDE`) 추가 |
| `public/app.html` | `data-view="kis-monitor"` 뷰: 상태 배지 줄, KPI 8개(총평가·계좌손익·봇 실현손익·당일 실주문/체결률·매수/매도·미해소·봇 보유/전체·정합성), 보유 표, 정합성 패널, 실주문 표, 사이클 이력. 60초 자동 새로고침 토글 + 수동 새로고침 |
| `public/js/kis_monitor.js` 신설, `main.js` 연결 | 뷰 진입 시 로드, 뷰 이탈 시 타이머 정지(`lumina:view-changed`) |
| 테스트 | `tests/test_kis_monitor_route.py` 3건(실현손익 평균단가법, 전체 조립, 게이트웨이 없음). 전체 **225 passed** |

**확인**: 배포 후 로그인 → 로보 어드바이저 > KIS 모의투자결과. 실주문 표의 02:43 원익IPS FILLED·02:51 브이티 LOST, 사이클 이력의 거래별 "실주문 없음/error(사유)" 가 지금 겪는 "가상 2건/실주문 ≤1건" 원인을 바로 보여준다.

### 6-18. 2026-10-06 리밸런싱 엔진 → KIS 투자 종목별·섹터별 리밸런싱 (사용자 요청 2건)

**요구 변화**: ① "KIS 모의투자 DB 를 이용" → ② "모의계좌(현금+주식)를 목표 비중으로 되돌리는 것이 아니라 **KIS 투자 종목별, 섹터별 리밸런싱**".

| 변경 | 내용 |
|------|------|
| 계좌 소스 `account_source`(사용자별, data_cache `rebalance:source:<uid>`) | `kis`(기본 — 게이트웨이 설정 시) / `paper`. kis: 현금·보유 = st 게이트웨이 KIS Testbed 잔고, 미보유 목표 시세 = `stock.get_quote`(KIS 우선), 실행 = `auto_trade._place_live_order_via_gateway`(live_orders 추적, 공격모드면 시장가, 장외 skipped). RebalanceRun.orders 에 `live_order{order_no,client_order_id,…}`, note `[kis]` 접두. 스키마 변경 없음 |
| **섹터 목표** `targets` 항목 `symbol="SECTOR:반도체"` | 1차 배분 = 섹터 비중(반도체·IT·K뷰티, 합 ≤ 100, 나머지 현금). 섹터 안: 명시 종목 목표 먼저, 잔여 R 을 그 섹터의 **보유 종목에 균등**(미보유 섹터는 유니버스 상위 2종목으로 채움, `SECTOR_FILL_CANDIDATES`). 검증: 섹터 내 종목 합 ≤ 섹터 목표, 섹터 이름은 `QUANT_SECTORS` |
| `snapshot()` | `sectors[]`(섹터별 평가액·현재/목표 비중·이탈·종목·explicit), 행에 `sector`·`target_derived`. 최대 이탈은 종목·섹터·현금 중 최대 → DRIFT 트리거가 섹터 이탈에도 반응. 유니버스 밖 보유는 `기타`(목표 0 → 플랜 외 전량 매도 후보) |
| `execute()` | before/target/after 가중치에 `SECTOR:*` 포함 |
| 라우트 | `PUT /plan.account_source`, `GET /plan`·`/status` 에 `account_source`·`sector_targets`·`symbol_targets`. `resolve_targets` 는 SECTOR 항목 통과 |
| 화면(`robo-rebalance`) | 「리밸런싱 대상 계좌」 선택(KIS/내부), **섹터 목표 입력 3칸**, 종목 목표는 "섹터 안 고정"(선택), 「보유 비중 불러오기」는 섹터 비중으로 복사, 현황에 **섹터별 비중 표** + 종목 표(섹터·"(섹터 배분)" 표시), KIS 실행 확인문·소스 배지 |
| 테스트 | `tests/test_rebalance_kis.py` 10건(소스 기본/검증, KIS 스냅샷·심볼 매핑, KIS 시세 제안, 게이트웨이 실행 submitted/skipped/failed, 섹터 파싱·검증, 섹터→종목 배분, 섹터 집계, 섹터 가중치 기록). 전체 **238 passed** |

**사용법**: 리밸런싱 엔진 → 대상 계좌 KIS → 섹터 비중 입력(예: 반도체 40 / IT 35 / K뷰티 20, 현금 5) → 저장 → 「주문 제안」 → 「실행」(장중). 기존 보유 중 유니버스 밖 종목(KR모터스·POSCO 등)은 `기타` 섹터로 묶여 **플랜 외 전량 매도 후보**가 되므로, 팔고 싶지 않으면 종목 목표를 따로 넣어 비중을 고정할 것(7절 L20).

### 6-19. 2026-10-06 `#robo-screening`(패턴 인식·종목 스크리닝) 운영 기능 테스트 (사용자 요청) — **실패, 원인 2건**

**방법**: fd.edumgt.co.kr 공개 HTTP 로만 점검(서버 로그·DB 미열람). 배포본 `app.html`·`js/robo.js` 는 로컬과 md5 동일. 화면이 부르는 `GET /api/stocks/signals`(비로그인, 인증 의존성 없음) 를 모델 4종 × 신호 3종으로 호출하고, 31종목 일봉을 `/api/stocks/candles` 로 받아 `screen_pattern` 을 로컬에서 모델별로 실행.

| 결과 | 내용 |
|------|------|
| 화면 | 「스크리닝 실행」 → 요청이 **3~4분 이상 응답 없음**, 끝나도 **500**. 카드는 "시그널 데이터를 불러올 수 없습니다", 표는 빈 상태로 남음(`loadRoboScreening` 의 catch 가 오류 메시지를 삼킴) |
| 원인 ① 지연 | `get_quote` 가 국내 종목마다 st `GET /api/kis-chart/candles` 를 먼저 부르고 `KIS_CHART_TIMEOUT=10` 초 뒤 Yahoo 로 폴백. **st 백엔드가 응답 없음**(`https://st.edumgt.co.kr/api/health`·`/api/kis-chart/*`·`/openapi/v1/kis/*` 모두 20~30초 이상 무응답, 정적 `/`·`/docs` 만 200) → 종목당 10.4초 × 31 = 약 5.4분, 그것도 **순차 루프**. 현재가는 캐시도 없다(일봉은 6시간 캐시) |
| 원인 ② 500 | 20번째 종목 더존비즈온 `012510.KQ` 가 KIS 실패 후 Yahoo 에서도 **캔들 0건**(Yahoo 는 .KQ 로만 존재하고 최근 시세 없음 — 2020년 코스피 이전 종목). `screen_pattern([])` → `indicators` 의 `preprocess` 가 `KeyError: 'time'` → 라우트에 예외 처리 없어 500. 로컬 재현: 나머지 30종목은 4개 모델 모두 정상 계산 |
| 부수 | Yahoo 폴백 `get_quote` 는 `change`/`change_pct=None` 고정 → 카드 "--%", 표 "+0.00%" 로 표시. `rsi` 모델 점수식 `(30-RSI)/10` 은 RSI 50 전후 중립 구간을 **SELL 80~95%** 로 매긴다(31종목 중 22종목 SELL). 운영 영향 더 큰 것: st `/openapi/v1/kis/*` 무응답이면 **KIS 실주문 게이트웨이도 같은 상태**(45초 타임아웃 → UNKNOWN 재전송 경로) |

**권고(미적용, 결정 필요)**
1. st 서버 python-backend 상태 확인·재기동(사용자): `ssh ubuntu@43.202.161.134 "sudo docker ps; sudo docker logs --since 30m <python-backend> | tail -50"`. 복구 후 `curl -m 15 https://st.edumgt.co.kr/api/health` 200 이면 스크리닝은 종목당 1초 이내로 돌아온다.
2. 코드(lumina, 소규모): `stock_signals` 에서 `candles` 빈 경우 `continue`(또는 `screen_pattern` 첫 줄 빈 리스트 가드) · 31종목을 `asyncio.gather` 로 병렬 조회 + 현재가 1~5분 캐시 · Yahoo 폴백 `change_pct` 를 `price/prev_close` 로 계산 · `loadRoboScreening` catch 에 오류 문구 표시.
3. `012510.KQ` 는 KIS 경로(접미사 무관 코드 012510)로는 조회되므로 유니버스 유지 가능, 단 Yahoo 폴백만으로는 항상 빈 값 → 2번 가드가 전제.
4. `rsi` 모델 점수식은 30/70 밴드 밖에서만 점수를 주도록 바꿀지 결정(현 식은 "역추세" 라벨과 맞지 않음).

**확인(사용자)**: st 복구 후 `curl -m 60 "https://fd.edumgt.co.kr/api/stocks/signals?signal=all&model=lightgbm&min_confidence=50"` 이 수 초 내 200 이고 `count` 가 30 이상이면 ①은 해소. 500 이 계속이면 ② 가드 적용 전까지는 더존비즈온 한 종목 때문에 전체가 실패한다.

**6-19 후속(07:06Z, 권고 1 시행)**: st python-backend 는 멈춘 게 아니라 **동기 핸들러 스레드풀(40) 고갈**이었다. 원인은 lumina 5분 사이클의 분봉 31종목 × KIS 8회(1.05초 직렬 간격) ≈ 260초/사이클 + 10초 클라이언트 타임아웃 뒤 유령 처리 — 상세·수치·권고는 **stock-coin-trade todo 6-9**. `docker restart crypto-mock-python` 후 fd 현재가 0.07초(`source=kis`, 등락률 정상), `/api/stocks/signals` **6조합 모두 200·31종목**(더존비즈온은 KIS 경로로 캔들 수신 → 500 소멸). 응답 32~54초(순차 31종목 × 1초, KIS 레인 대기)로 **동작은 하지만 느림** → 6-19 권고 2(병렬·캐시·빈 캔들 가드)는 여전히 유효. 재기동 6분 뒤 분봉 499 재발 시작 → **재발 방지 결정 필요**: 최소 조치는 fd `.env` `KIS_CHART_MINUTES=60`, `KIS_CHART_TIMEOUT=30` + 장외 분봉 수집 생략(7절 L21).

### 6-20. 2026-10-06 스크리닝 화면 — 실행 중 모래시계·경과 시간, 「설명」 모달 팝업 (사용자 요청 2건) + L21 env 적용 시도

| 항목 | 파일 | 내용 |
|------|------|------|
| 실행 중 표시 | `public/js/robo.js` `startScreenHourglass/stopScreenHourglass`, `public/css/app.css` `.screen-loading .hourglass` | 「스크리닝 실행」 클릭 시 카드 영역에 CSS 애니메이션 모래시계(⏳ 회전 + 모래 바)와 0.1초 단위 경과 시간, 버튼도 `⏳ 12.3초` 로 바뀌며 비활성(중복 실행 방지). 완료/실패 시 `#screen-status` 에 `✔ 완료 — N종목 (모델/신호/신뢰도) · 소요 34.2초 · 시각` 또는 `✖ 실패 — 사유` 표시(기존 catch 가 삼키던 오류 노출). `prefers-reduced-motion` 이면 정지 |
| 설명 모달 | `public/app.html` `#xai-modal`, `app.css` `#xai-modal*`, `robo.js` `openXaiModal/closeXaiModal/showScreenXai` | 결과 표의 「🧠 설명」이 표 아래 인라인 패널 대신 모달 팝업으로 열림. 로딩 중 같은 모래시계+경과 시간, 완료 시 종목명·5일 예측·모델·신뢰도·계산 시간 + `renderXaiBlock`. 닫기: ✕·배경 클릭·Esc. 기다리다 닫으면 결과를 덮어쓰지 않음. 인라인 `#screen-xai-panel` 제거 |
| 배포 | fd | rsync 3파일 → `compose up -d --build app`. 서버·컨테이너·공개 URL md5 = 로컬(`robo.js f57484ce…`). 미커밋. 브라우저 실행 검증은 미수행(에이전트 환경에 node·브라우저 없음, 괄호·템플릿 균형 검사만) → **사용자 확인**: `#robo-screening` 에서 실행 → 모래시계·초 표시 → 결과 → 「설명」 모달 |
| 주의 | fd 서버 | 이전 세션 rsync 잔재 `public/public/`(9/29) 삭제함. 이번에도 `--relative` 로 한 번 잘못 올라가 즉시 정리 |

**L21 env 적용(실패 — 사용자 수행 필요)**: fd `.env` 에 `KIS_CHART_MINUTES=60`·`KIS_CHART_TIMEOUT=30` 추가 + app·celery 재기동은 자동 모드 분류기(운영 배포)로 차단. 사용자 명령:
```bash
ssh -i lumina-invest/fd.edumgt.co.kr.pem ubuntu@43.201.229.188 "cd /home/ubuntu/lumina-invest && printf '\nKIS_CHART_MINUTES=60\nKIS_CHART_TIMEOUT=30\n' >> .env && sudo env COMPOSE_FILE='docker-compose.yml:compose.fd.yml' docker compose up -d app celery-worker celery-beat && sleep 8 && sudo docker exec fin-ai-celery-worker env | grep ^KIS_CHART_"
```
적용 확인: st 쪽 `docker logs crypto-mock-frontend | grep -c " 499 "` 가 사이클당 0~1건으로 떨어지고, 분봉 완료 간격이 8초→2초.

**6-20 후속(사용자 요청)**: 스크리닝 대기 화면을 **모달**로 이동 — 「스크리닝 실행」 클릭 시 `#xai-modal` 을 재사용해 `⏳ 스크리닝 실행 중` 창(모래시계 + 대기 시간 0.1초 갱신 + 조건 요약)을 띄우고, 완료·실패 시 자동으로 닫고 결과·상태 줄 표시. 창을 닫아도 실행은 계속(버튼·카드 영역에 경과 시간 유지). 화면 진입 시 **자동 실행 제거**(`main.js` → `renderScreenIdle` 안내 문구만) — 30초 이상 걸리는 호출이 탭만 열어도 나가던 문제 해소. 파일: `robo.js`(`renderScreenIdle/startScreenHourglass/stopScreenHourglass`, export 추가), `main.js`. fd 재배포(컨테이너·공개 URL md5 = 로컬 `robo.js 69657378…`, `main.js 99bae07a…`), 미커밋. 브라우저 검증은 사용자.

### 6-21. 2026-10-06 스크리닝 — 「중지」 버튼 + 1종목씩 검사·진행 표시 (사용자 요청)

| 항목 | 파일 | 내용 |
|------|------|------|
| 백엔드 | `app/routes/stocks.py` `stock_signals` | `symbols=`(쉼표 구분) 파라미터 추가 → 해당 종목만 계산. **빈 캔들 가드** 추가(`012510.KQ` Yahoo 폴백 0건이던 500 원인 제거, 해당 종목만 건너뜀·경고 로그). 응답 형식 동일, 기존 호출(전체)도 그대로 |
| 진행 방식 | `public/js/robo.js` `loadRoboScreening` | `/api/quant/ml/stocks` 로 유니버스(31) 받은 뒤 **한 종목씩** `/api/stocks/signals?symbols=X&signal=all&min_confidence=0` 호출 → 신호·신뢰도 필터는 화면에서 적용(제외된 종목도 점수 표시). 종목마다 모달 목록에 `⏳ 검사 중 → ✔ BUY 81% / – 제외(HOLD 55%) / ✖ 오류` 체크, 진행 바 `n / 31`, 카드·표는 종목 끝날 때마다 갱신(신뢰도·|점수| 정렬) |
| 중지 | `app.html` `#screen-stop-btn`(실행 버튼 옆, 실행 중만 표시), 모달 안 `#screen-modal-stop` | 누르면 "중지 중…" → 지금 검사 중인 종목까지 끝내고 멈춤. 상태 줄 `✔ 중지 — 12/31종목 검사, 5종목 통과 (조건) · 소요 n초`. 창을 닫아도 실행·중지 버튼은 페이지에 남음 |
| 배포 | fd | `py_compile` 통과 → rsync 3파일 → `compose up -d --build app`. 컨테이너·공개 URL md5 = 로컬(`robo.js 3cfb3ba7…`, `stocks.py 47b7320e…`). 실서버 확인: `symbols=005930.KS` 200·1건(2.2초), `symbols=012510.KQ` 200·HOLD(이전 500 → 해소, 등락률 None 은 Yahoo 폴백 한계), 2종목 3.9초, 없는 코드 0건. 미커밋 |
| 소요 | — | 종목당 약 2초(KIS 레인 대기 포함) → 전체 약 60~70초. 일괄 호출(32~54초)보다 길지만 진행이 보이고 중간 중지 가능. 줄이려면 6-19 권고 2(현재가 캐시) 또는 L21 env 적용 |

**확인(사용자)**: `#robo-screening` → 실행 → 모달에 종목별 체크가 한 줄씩 늘어나는지 → 「중지」 → 상태 줄 "중지 — n/31" → 다시 실행 → 완료 → 「설명」 모달. 브라우저 실행 검증은 에이전트 환경에서 불가(괄호·템플릿 균형, 함수 정의 수 검사만).

### 6-22. 2026-10-07 설정 변경 — 1회 50만 원 · 3분 사이클 · 공격 모드 강화 (사용자 요청 "1회 50만원, 3분마다 공격적으로")

**현황 확인**: 공격 모드(`QUANT_AGGRESSIVE_MODE`)·배치(`KIS_PAPER_BATCH_ENABLED`)는 fd `compose.fd.yml` 기본값으로 이미 ON. 바꿀 것은 ① 1회 투자금 30만→50만, ② 사이클 5분→3분, ③ 3분 주기에 맞춘 쿨다운·캐시·일 주문 수. 단, **배치 행(시스템 사용자)은 DB 에 30만 원이 이미 저장**돼 있고 `ensure_system_batch` 가 기존 행의 한도·종목을 덮어쓰지 않는 설계라 env 만 바꿔서는 50만 원이 되지 않는다 → 배치 행의 투자금·종목은 env 를 정본으로 매 사이클 동기화하도록 변경.

| 변경 | 내용 |
|------|------|
| `app/config.py` | `QUANT_CYCLE_SEC=180` 신설(사이클 주기 단일 출처). `KIS_PAPER_BATCH_PER_TRADE_BUDGET` 300,000→**500,000**. `QUANT_AGGRESSIVE_COOLDOWN_MIN` 5→**3**, `QUANT_AGGRESSIVE_CACHE_MIN` 4→**2**(3분 사이클마다 새 분봉), `QUANT_AGGRESSIVE_MAX_ORDERS_PER_DAY` 200→**300**(장중 130사이클×≤5건=650 보다 작게) |
| `app/celery_app.py` | beat 항목 키 `quant-auto-trade-5min`→`quant-auto-trade-cycle`, `schedule=QUANT_CYCLE_SEC`(180), `expires=주기-20`(160) |
| `app/tasks/sync_tasks.py` | `quant.auto_trade_cycle` `time_limit=주기-15`(165초). 6-21 기준 31종목 시그널 ≈ 60~70초라 여유 있음. 초과 시 그 사이클만 중단·다음 주기 재시도 |
| `app/services/auto_trade.py` | `_INTERVAL_SEC=QUANT_CYCLE_SEC`, 상태 문구 `celery-beat (3분)` 동적 |
| `app/services/kis_batch.py` | `_sync_env(row)`: 기존 배치 행의 `quant_per_trade_budget`·`quant_ai_top_n`·`quant_symbol_source`·`quant_selected_symbols` 를 매 사이클 env 값으로 맞춤(바뀐 필드만 commit, 응답 `synced`). **위험 한도(비중·일손실·쿨다운·일 주문 수)는 그대로**(쿨다운·일 주문 수는 공격 모드가 런타임에 덮어씀). 재시작 audit 은 발생하지 않음 |
| `app/services/kis_quickstart.py` | `TESTBED_DEFAULTS.quant_per_trade_budget` 500,000. `interval_min()`·`effective_defaults()` 신설 — 대시보드 「설정 보기」가 공격 모드 적용값(쿨다운 3분·일 300건)을 보여 주도록 `readiness()` 에 `defaults`(적용값)·`interval_min`·`aggressive` 추가. `start()` 응답의 risk·interval_min 도 적용값 |
| `app/routes/health.py`·`kis_monitor.py`·`stocks.py`·`notification.py` | `cycle_sec`·문구를 `QUANT_CYCLE_SEC` 기준으로 |
| `public/js/dashboard.js`·`core.js`·`kis_monitor.js`·`app.html` | 문구 "10분/5분" → 서버 `interval_min`(기본 3분), 공격 모드 배지, 1회 50만 원·쿨다운 3분·일 주문 300건 표시 |
| `.env.example`·`readme.md` | 새 기본값·`QUANT_CYCLE_SEC` 문서화 |
| 테스트 | `test_universe_and_schedule.py`(3분·time_limit·health), `test_kis_batch.py` 기존 행 동기화 2건(한도 유지 검증 포함), quickstart 50만 원. 전체 **237 passed** |

**fd 반영 방법(에이전트는 서버를 만지지 않음)**: push → `deploy.yml` 자동 배포(celery-beat·worker 재기동으로 새 스케줄 적용). 서버 `.env` 에 `KIS_PAPER_BATCH_PER_TRADE_BUDGET`·`QUANT_AGGRESSIVE_COOLDOWN_MIN`·`QUANT_AGGRESSIVE_CACHE_MIN`·`QUANT_AGGRESSIVE_MAX_ORDERS_PER_DAY`·`QUANT_CYCLE_SEC` 가 **명시돼 있으면 그 값이 코드 기본값보다 우선**하므로 있으면 지우거나 새 값으로 고칠 것(6-13 진단 당시 워커 env 에는 두 스위치만 있었음). 배포 후 확인:
```bash
curl -s https://fd.edumgt.co.kr/api/health | python3 -m json.tool       # quant.cycle_sec=180, aggressive_mode=true
ssh -i lumina-invest/fd.edumgt.co.kr.pem ubuntu@43.201.229.188 "sudo docker logs --since 10m fin-ai-celery-worker 2>&1 | grep -E '배치 설정을 env 에 맞춤|auto_trade_cycle' | tail -5"
sudo docker exec fin-ai-postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select user_id, quant_per_trade_budget, quant_ai_top_n, risk_max_position_pct from broker_settings where broker='"'"'kis'"'"';"'   # 시스템 행 500000
```
첫 사이클 로그에 `KIS 모의투자 배치 설정을 env 에 맞춤: ['quant_per_trade_budget'] (1회 투자금 500000원, AI 3종목)` 이 한 번 찍히고 이후엔 조용하다.

**주의**
- **종목 비중 20%** 는 유지 → 1회 50만 원은 "총자산×20% − 기존 보유" 안에서만 집행된다. Testbed 총자산이 250만 원 미만이면 수량이 깎이거나 `[위험관리 생략]` 이 뜬다(L22).
- 쿨다운 3분 = 사이클당 같은 종목·방향 1회. 멱등키는 분 단위라 3분 사이클에서도 유일.
- 3분 사이클은 5분봉의 같은 봉을 두 번 볼 수 있다(KIS 1분봉→5분 집계). 시그널이 같으면 강제 로테이션 매수가 더 자주 걸릴 수 있음 → 손절 빈도 관찰(L16).
- `confirm_fills` 2분·`quant.reconcile` 10분은 그대로. 브라우저 실행 검증은 에이전트 환경에서 불가(node 없음) — JS 는 문자열 치환만.

### 6-23. 2026-10-07 KIS 모의투자결과 「봇 실주문」 그리드 — 봇/사용자 구분 컬럼 + 검색·정렬·페이지·CSV (사용자 요청)

| 변경 | 내용 |
|------|------|
| `app/routes/kis_monitor.py` | `GET /api/quant/kis/orders` 신설: `owner=all|batch|me`, `status=`(쉼표 목록, 대소문자 무시), `side=BUY|SELL`, `q=`(종목코드·.KS/.KQ 표기·종목명·주문번호·clientOrderId·메모 부분 일치), `date_from/date_to`(KST 일자, 종료일 포함, 형식 오류·역전은 400), `limit≤500`, `offset`. DB 에서 소유자·기간으로 최신순 최대 2,000건을 받아 나머지를 파이썬에서 거르고 최신순 정렬 후 페이지. 응답 `total·rows·counts_by_owner·counts_by_status·truncated·filters`. `_order_dict` 에 `owner_label`(봇(배치)/사용자) 추가, `/monitor` 의 `orders.today_owner_counts` 추가 |
| `public/app.html`·`public/js/kis_monitor.js` | 그리드 위 검색 툴바(구분·상태(단일/열린 주문/미해결·실패 묶음)·방향·기간·검색어·건수·검색·초기화·CSV·이전/다음). 「구분」 컬럼(봇=보라, 사용자=초록 배지)·「주문번호」 컬럼(툴팁 clientOrderId) 추가. 헤더 클릭 정렬(클라이언트), 결과 요약 "검색 결과 N건 (봇 a · 사용자 b) · 상태 …", 카드 제목 옆 당일 요약에 봇/사용자 건수. 60초 자동 새로고침은 현재 검색 조건·페이지를 유지한 채 갱신. CSV 는 현재 정렬 결과를 BOM 포함 UTF-8 로 내려받기 |
| 테스트 | `test_kis_monitor_route.py` +2(구분·상태·방향·텍스트·페이지 / KST 기간·400·422), 기존 모니터 테스트에 owner 집계 단언. 전체 **239 passed** |

**검증(배포 후)**: `#kis-monitor` → 구분 "봇(배치)" 선택 시 사용자 주문이 사라지고 요약 건수가 맞는지, 검색어 `018290` 과 `브이티` 가 같은 결과인지, 기간을 오늘로 좁히면 당일 요약과 같은 건수인지, CSV 열림. `/js` 는 no-cache 미들웨어라 버전 쿼리 불필요.

### 6-25. 2026-10-07 LNB 메뉴 3개 제거 (사용자 요청)

`public/js/core.js` NAV 로보 어드바이저 그룹에서 「신용 리스크 분석(agent-cb)」「맞춤 상품 추천(agent-products)」「투자 정보 리서치(agent-news)」 항목 제거. 뷰 마크업·라우팅·API 는 그대로 두어 `#agent-cb` 등 해시로는 여전히 열린다(메뉴에서만 숨김). 되돌리려면 세 줄 복원.

### 6-26. 2026-10-07 「증권사 API 자동화」「TradingView 연동」 메뉴를 시스템관리(더보기 오프캔버스)로 이동 (사용자 요청)

`public/js/core.js` GNB_MENUS: 두 항목을 `company`(투자 인디케이터 LNB) 에서 `sysadmin` 그룹으로 옮김. 오프캔버스는 agent·company 를 제외한 그룹을 그리므로 자동으로 「시스템관리」 아래에 표시되고, 해당 뷰를 열면 LNB 제목이 시스템관리로 바뀐다. 뷰·라우팅·초기화(`main.js` initTradingViewView 등)는 변경 없음.

### 6-27. 2026-10-07 공통 타이포그래피 가이드 — 타이틀 Pretendard 18px 고정, 18px 초과 금지 (사용자 요청, 4개 사이트 공통)

| 변경 | 내용 |
|------|------|
| `public/css/app.css` | 파일 맨 위에 공통 가이드 주석(4항), 주 CSS 맨 끝에 「타이틀 고정」 블록: `--title-size:18px`·`--title-font: Pretendard…`, `h1, h2, .page-title { font-size:18px !important; font-family: Pretendard !important }`(인라인·유틸리티 클래스보다 우선), `h1/h2` 안의 mark·small·span 은 inherit |
| 적용 범위 | 타이틀(h1·h2)만 강제. 본문·KPI 숫자 등 기존 18px 초과 선언은 그대로 두었다(아래 수치) — 가이드 2항에 따라 새 규칙에서는 금지, 기존 값은 화면별로 줄여 나간다 |

같은 블록이 pr(`frontend/style.css`)·fd(`public/css/app.css`)·st(`frontend/css/style.css`, 가이드 주석은 `kis-practice.css` 에도)·iv(`frontend/style.css`, `investment-native/styles.css`) 에 들어 있다. 캐시 버전이 있는 링크는 각 페이지에서 갱신 필요(st `style.css?v=…`, pr/iv `style.css?v=…`); fd `/css` 는 no-cache.

### 6-28. 2026-10-08 pr.edumgt.co.kr 라우트가 fd 배포마다 사라지는 문제 (근본 수정)

증상: pr 컨테이너(`domain-rag-lab-api-1`)는 healthy·shared-net 연결 정상인데 `https://pr.edumgt.co.kr/health` 가 연결 실패. 서버 Caddy 로그의 `enabling automatic TLS certificate management domains:["fd.edumgt.co.kr"]` 로 pr 블록 자체가 설정에 없음을 확인. 서버 `infra/fd-edumgt/Caddyfile` 수정 시각 2026-10-07 08:37.

| 변경 | 내용 |
|------|------|
| `infra/fd-edumgt/Caddyfile` | `pr.edumgt.co.kr → reverse_proxy pr-api:8000` 블록 추가 + 경고 주석 |

원인: `.github/workflows/deploy.yml` 의 코드 동기화가 `rsync -az --delete` 로 저장소 전체를 서버에 덮어쓰는데, 이 Caddyfile 에 pr 블록이 없었다. 그래서 fd 를 배포할 때마다 서버에서 수동으로 넣어 둔 pr 블록이 날아가고 pr 사이트만 끊겼다(반복 재발의 진짜 이유). 이제 저장소가 정본이므로 다음 fd 배포부터 pr 라우트가 유지된다.

즉시 복구(서버에서 1회, 이 커밋을 배포하기 전이라면):

```bash
ssh -i /home/ubuntu/lumina-invest/fd.edumgt.co.kr.pem ubuntu@43.201.229.188
f=/home/ubuntu/lumina-invest/infra/fd-edumgt/Caddyfile
cp -p $f ~/Caddyfile.bak.$(date +%s)
printf '\npr.edumgt.co.kr {\n    encode zstd gzip\n    reverse_proxy pr-api:8000\n}\n' | tee -a $f   # bind mount 이므로 같은 inode 유지(tee, sed -i 금지)
sudo docker exec lumina-invest-proxy-1 caddy reload --config /etc/caddy/Caddyfile
curl -sk --resolve pr.edumgt.co.kr:443:43.201.229.188 -o /dev/null -w '%{http_code}\n' https://pr.edumgt.co.kr/health
```

참고: 기존 README(`domain-rag-lab/deploy/pr-edumgt/README.md`)의 CloudFront 원본용 `http://ec2-…compute.amazonaws.com` 블록은 CloudFront 를 쓰지 않으므로 넣지 않았다.

### 6-29. 2026-10-08 자유 산식 지표(#indicator-formula) Pine 내보내기 — v6 전환 + 문법 오류 교정 (사용자 요청)

`to_pine` 이 정규식 치환으로 코드를 만들어 TradingView 에서 컴파일되지 않는 출력이 여러 갈래로 나왔다. 생성기를 DSL AST 를 직접 순회하는 방식으로 바꾸고 `//@version=6` 으로 올렸다.

| 변경 | 내용 |
|------|------|
| `app/services/formula.py` | `_PINE_SPEC`(DSL 36개 함수 → v6 식 매핑)·`_pine_expr`(AST→Pine)·`_pine_len`·`_pine_hist`·`_pine_length_params` 신설, `to_pine` 재작성. `_PINE_FUNCS` 와 `iff_` 헬퍼·정규식 치환 제거. 모듈 상단에 `import re` 추가 |
| `app/routes/formula.py` | `/export` 에서 `FormulaError` → 422 (변환 불가 시 깨진 코드 대신 메시지) |
| `tests/test_formula.py` | v6 단언으로 교체 + 4개 신설(문법 함정·bool 캐스팅·파라미터 입력형·중첩 과거참조). 전체 **248 passed** |

고친 문법 오류(모두 TradingView 컴파일 실패였다):

| 증상 | 원인 | 조치 |
|------|------|------|
| `close ^ 2` | `**` → `^` 치환. Pine 에는 거듭제곱 연산자가 없다 | `math.pow(a, b)` |
| `ta.obv()` | `obv` 를 함수로 매핑. `ta.obv` 는 내장 **변수** | `ta.obv` (괄호 없음) |
| `ta.sma(close, n)` + `n = input.float(20.0)` | length 는 `series int` 인데 실수 입력을 넘김 | 기간 자리에 쓰인 파라미터만 `input.int`, 식이면 `int(...)` |
| `macd_signal(...)`·`mean`·`normalize`·`rank`·`clip`·`typical`·`vwap` 미변환 | `_PINE_FUNCS` 누락 8개 → Undeclared identifier | 전부 매핑(36/36). 미지원 함수는 `FormulaError` |
| `shift(sma(close,5), 3)` 변환 실패 | 정규식 `[^,()]+` 가 중첩 괄호를 못 받음 | AST 순회로 해결 → `(ta.sma(close, 5))[3]` |
| `30 < rsi(close,14) < 70` | Pine 은 연쇄 비교가 없다 | `(30 < x) and (x < 70)` 로 분해 |
| `True`·`pi` | Pine 은 `true`·`math.pi` | 상수/이름 매핑 |
| `shift(shift(close,1),2)` → `close[1][2]` | Pine 은 같은 값에 `[]` 를 한 번만 허용 | 변환 거부(FormulaError, 기간 합치라고 안내) |
| 숫자 산식을 buy/sell 로 쓰면 plotshape 오류 | v6 는 숫자→bool 암묵 변환을 없앴다 | bool 이 아닌 신호만 `bool(...)` 로 캐스팅 |

근사치로 남긴 부분(생성 코드 주석에 명시): `bb_*` 는 `ta.stdev`(표본), `rank()` 는 `ta.percentrank/100`(`<=` 기준), `vwap(n)` 은 세션 `ta.vwap` 이 아니라 hlc3·거래량 n봉 롤링 가중평균.

`public/js/indicator.js`(#indicator-custom)의 Pine 생성기는 이미 v6 이고 따로 문법 문제가 없어 손대지 않았다.

### 6-30. 2026-10-08 모의 투자 의사결정(#robo-decision) 「판단 근거」 — 실제 시장 데이터 기반임을 보장·표시 (사용자 요청)

요구사항: 판단 근거가 실제 주식 데이터 기반으로 판단되어야 함. 확인 결과 근거 문구는 전부 실제 수치에서 생성되고 있었으나(아래 "검증"), **데이터를 못 받은 종목이 화면에서 「관망 — 추세 확인 중」으로 보이는** 경로가 있어 근거 없는 판단으로 읽혔다. 또 근거가 어느 데이터에서 나왔는지 화면에 전혀 없었다.

| 변경 | 내용 |
|------|------|
| `app/services/stock.py` | `get_candles` 응답에 `source`(kis/yahoo) 기록. `get_quant_indicators` 에 `interval="1d"`·`bars`·`as_of`(마지막 봉 epoch)·`source` 추가 |
| `app/services/aggressive_mode.py` | `get_intraday_indicators` 에 `as_of`·`price_source`(kis_live/last_close) 추가. 분봉이 없을 때 `action` 을 「관망」 → **「판단 불가」**(+`error`)로 바꿔 판단한 척하지 않게 |
| `app/services/auto_trade.py` | `_signal_basis()` 신설, 사이클이 각 시그널에 `basis`(출처·봉 종류·봉 수·마지막 봉 시각)와 `error` 를 함께 기록. 지표 계산 실패 종목도 「판단 불가」 + 사유로 기록(종전에는 symbol·error 만 남아 화면에서 관망으로 보였다) |
| `app/routes/stocks.py` | `/quant/auto/status` 가 `error` 있는 시그널을 `signal:"NONE"`(관망과 구분)으로 내고, 각 시그널에 `cycle_time` 부착 |
| `public/js/robo.js` | `renderRoboRationale` 재작성: 카드마다 `📡 출처 · 봉 종류 · 봉 수 · HH:MM 기준 (N분 전)` 표시, NONE 은 「판단 불가」(빨강)로 맨 뒤 정렬, **reasons 가 비면 임의 문구를 만들지 않음**(종전 "관망 — 추세 확인 중"·"매수 시그널" 제거) |
| 테스트 | `test_aggressive_mode.py` 2개(판단 불가 계약·데이터 출처), `test_auto_status_batch_merge.py` 1개(NONE·basis·cycle_time 전달), `test_kis_market_data.py` 단언 갱신. 전체 **250 passed** |

**검증(서버 실측, 2026-10-08 10:05~10:17 KST)**: `QUANT_AGGRESSIVE_MODE=true` 로 5분봉 사용. `data_cache` 의 `candles:005930.KS:5d:5m` 에 삼성전자 5분봉이 실제로 적재돼 있고(267,500 → 266,500원, 봉별 거래량 8만~25만주), 마지막 봉 시각이 조회 시점과 1~4분 차이. 근거 문구는 `score_intraday()`·`_generate_signal()` 이 그 종가 배열로 계산한 RSI·MA5/MA20·모멘텀 수치 그대로다. 신호 경로에 난수·더미·하드코딩 데이터는 없다(`mock` 은 체결 브로커 이름일 뿐 시세와 무관).

**남은 데이터 품질 이슈(미수정, 거래 로직이 바뀌므로 판단 필요)**: Yahoo 분봉 응답의 **마지막 봉은 미완성 의사(擬似) 봉**이다 — 5분 경계가 아닌 현재 시각(`time: 1791421035`), `volume: 0`, 시·고·저·종가가 모두 현재가. 현재가 자체는 실제값이라 `current_price` 로는 맞지만, 이 봉이 RSI·MA·모멘텀 계산에 한 점으로 들어가 지표를 약간 둔화시킨다. 완성봉만으로 지표를 계산하고 현재가는 따로 쓰려면 `_intraday_candles` 에서 마지막 봉을 분리해야 한다.

### 6-31. 2026-10-08 섹터 인디케이터(#company-sector) — 정적 목업을 실측 집계로 교체 + 투자 판단 지표·근거 보강 (사용자 요청)

종전 `loadCompanySector()` 는 **API 호출이 전혀 없는 하드코딩 배열**이었다(6개 섹터, PER·PBR·「전망」이 모두 가짜. `completion.js` 에도 "정적 목업 42%" 로 기재돼 있었다). 실제 데이터 기반으로 새로 만들고 지표를 대폭 늘렸다.

| 변경 | 내용 |
|------|------|
| `app/services/sector_indicators.py` (신설) | QUANT_STOCKS 31종목·3섹터를 펀더멘털(Yahoo quoteSummary)+2년 일봉에서 집계. 배수(PER·PBR·PSR·EV/EBITDA)는 **시총가중 조화평균 + 중위값** 동시 표기, 비율(ROE·ROA·마진·성장·D/E·유동비율)은 시총가중 산술평균, 가격은 **시총 비중 고정 섹터 인덱스**를 만들어 1/3/6/12개월 수익률·20일 변동성(연율)·52주 위치·RSI(14)·MA20/60 이격을 계산. 시장폭(20일선 위·60일선 위·1개월 상승 종목 비중), KOSPI(`^KS11`) 대비 초과수익, 섹터 간 상대점수(모멘텀30·수익성25·성장20·밸류15·안정성10, 백분위 가중합)와 판정(≥60 비중확대 / ≤40 비중축소), 수치를 인용한 근거 문장, 구성종목별 상세 지표 |
| `app/routes/stocks.py` | `GET /api/stocks/sectors?force=` 신설(1시간 캐시, 실패 시 502). 모듈 `logger` 추가 |
| `app/services/stock.py` | `get_candles` 응답에 `source` 기록(6-30과 공유). `get_fundamentals` 에 **`perBasis`**(trailing/forward) 추가 — 국내 종목은 Yahoo 가 trailingPE 를 거의 안 줘서 전부 forwardPE 로 채워지는데, 둘을 같은 'PER' 로 섞으면 판단이 달라진다 |
| `public/app.html`·`public/js/company.js` | 뷰 재구성: 헤더(유니버스·계산시각·캐시 여부·새로고침/재계산) → 벤치마크 수익률 → **섹터 비교 표**(상대점수·판정·시총·PER·ROE·매출성장·3개월·vs KOSPI·20일선 위·RSI) → 섹터별 상세 카드(팩터 점수 막대 + 밸류/수익성/성장·안정성/가격·시장폭 4그룹 22개 지표 + 「📌 판단 근거」 + 「📡 데이터 출처」 + 구성종목 접이식 표 13열). 결측은 **N/A + 커버리지 표기**(지어내지 않음) |
| `public/js/core.js`·`completion.js` | 화면 설명을 실제 내용으로 교체, 평가를 42% "정적 목업" → 86% "재무·가격 API 연결" |
| 테스트 | `tests/test_sector_indicators.py` 신설 4개(가중 집계의 결측·적자 제외, 백분위 방향, 섹터 인덱스 공통일자·비중, 일봉 결측 종목의 errors·점수·근거·구성종목 정렬). 전체 **261 passed** |

**실측 검증(2026-10-08, 라이브 Yahoo)**: 라우트 200. 반도체 상대점수 65.0(비중확대)·3개월 -21.96%·12개월 +305.28%·KOSPI 대비 12개월 +209.55%p·20일선 위 92%, K뷰티 50.0(중립)·3개월 초과 +19.11%p, IT 35.0(비중축소)·52주 위치 1.3%. KOSPI 1/3/6/12개월 +2.49/-16.63/+23.09/+95.73%.

**남은 제약(화면에 그대로 표기됨)**:
- **PBR 커버리지 0** — Yahoo 가 국내 상장사 `priceToBook` 을 주지 않는다. 그래서 밸류 팩터에 PSR·EV/EBITDA 를 함께 넣었고 PBR 은 N/A 로 표시한다.
- **PER 은 전량 forward** (`per_basis: {trailing: 0, forward: 11}`) — 카드의 📡 줄에 "PER 근거: 실적 n종목 / 전망 m종목" 으로 적는다.
- **더존비즈온(012510.KQ) 일봉 없음** — Yahoo 가 `.KQ` 로는 빈 배열, `.KS` 는 404 를 준다(상장시장 변경 추정). 섹터 인덱스에서 제외하고 `basis.errors` 에 표기. 유니버스 수정은 사용자 판단 필요.
- 섹터 인덱스 비중은 과거 시총 시계열이 없어 **현재 시총으로 고정**한 근사다(카드 집계 설명에 명시).
- 섹터가 3개뿐이라 상대점수의 백분위가 거칠다(33/50/83 등). 자동차·금융·2차전지 등 섹터를 늘리려면 QUANT_STOCKS 유니버스 확장이 필요 — 이는 자동매매 대상 종목도 같이 바뀌므로 사용자 결정 사항.

### 6-32. 2026-10-08 기본 인디케이터 전략(#indicator-strategy) — 조회 대기 모달 + KIS 모의투자 수동 거래 (사용자 요청)

요구: ① 전략 분석 클릭 후 데이터가 나올 때까지 모래시계 모달로 지연시간 표기, ② 거래 버튼을 활성화해 클릭 시 몇 주 거래할지 모달로 받고 KIS 모의투자 거래로 연결.

| 변경 | 내용 |
|------|------|
| `app/services/auto_trade.py` | `place_manual_kis_order()`·`ManualOrderBlocked`·`MANUAL_ORDER_MAX_QTY(10,000)` 신설. **자동매매와 같은 경로**(stock-coin-trade 게이트웨이 → `live_orders` 추적행 → 알림 → 감사로그 `order.manual_kis`)를 재사용하고, 게이트웨이 미설정 시 `kis_credentials` 직접 호출로 폴백. 가상계좌 체결은 만들지 않는다(사이클 포지션과 섞이면 성과 집계가 어긋남) |
| 〃 가드 | 매수/매도 외 거부 · 수량 1~10,000 · `resolve_route()` 미연동 거부 · **실전(real) 환경 거부**(모의 Testbed 에서만) · 비상정지 거부 · 현재가 조회 실패 시 주문 안 함. **가격은 서버가 `stock.get_quote` 로 정한다**(클라이언트 값 불신) |
| `app/routes/stocks.py` | `POST /api/stocks/quant/manual-order`(차단 사유는 422 + 사용자 문구), `GET /api/stocks/quant/order-readiness`(연동·환경·비상정지·장 운영시간·최대수량) |
| `public/app.html` | 「전략 분석」 옆에 `💰 KIS 모의투자 거래` 버튼(분석 전 `disabled`). 공통 대기 모달 `#app-loading-modal`(모래시계+경과초), 주문 모달 `#kis-trade-modal`(종목·현재가·신호 / 매수·매도 / 수량 입력 + 1·5·10·50주 / 예상금액 / 주문가능 안내) |
| `public/css/app.css` | `.app-modal`·`.app-modal-box` 공통 모달, `.app-hourglass` 회전(`prefers-reduced-motion` 존중), 대기 모달 z-index 10010(주문 모달 위), `button:disabled` 스타일 |
| `public/js/indicator.js` | `showLoadingModal/hideLoadingModal`(100ms 간격 경과시간 갱신), 분석 핸들러를 try/finally 로 감싸 버튼 잠금·모달 해제. 성공 시 `_tradeCtx`(종목·현재가·신호) 저장하고 거래 버튼 활성화(현재가 없으면 비활성). 주문 모달: 열 때 readiness 조회해 불가 사유 표시·전송 버튼 잠금, 장외시간 경고, confirm 후 전송, Escape·배경 클릭으로 닫기 |
| 테스트 | `tests/test_manual_kis_order.py` 신설 **11개**(side·수량 경계, 실전/미연동/비상정지/현재가없음 차단, 서버측 가격 사용과 게이트웨이 인자, 매도, 라우트 422 문구·바디 검증·결과 전달·readiness 사유). 전체 **272 passed** |

주의: 장 운영시간 밖에서는 게이트웨이가 `status: "skipped", reason: "market_closed"` 를 돌려주므로 주문이 나가지 않는다 — 모달에서 미리 경고하고, 전송 결과도 토스트로 구분해 보여 준다.

### 6-33. 2026-10-08 모의 투자 의사결정(#robo-decision) — 로그성 정보를 화면 하단으로 (사용자 요청)

`#robo-decision-log` 가 상단 제어 카드 안에 있어, 판단 결과(「📊 최근 AI 투자 판단 근거」)보다 로그가 먼저 보였다.

| 변경 | 내용 |
|------|------|
| `public/app.html` | 화면 순서를 **① 제어(제목·상태·시작/중지·계정·프로세스 안내) → ② 📊 최근 AI 투자 판단 근거 → ③ 🧾 실행 로그** 로 바꿈. 로그를 상단 카드에서 떼어 맨 아래 독립 카드로 옮기고, 로그 내용 설명 한 줄 추가(계좌 평가·체결·위험관리 생략·공격 모드 메모·정합성 점검). 상단 제어 바의 「로그 새로고침」 버튼도 로그 카드 헤더로 이동(중복 없음) |

id(`robo-decision-log`·`robo-decision-refresh`)는 그대로라 `robo.js` 의 바인딩·렌더 로직은 변경 없음. 전체 **272 passed**(백엔드 영향 없음), robo.js 문법 검사 통과.
