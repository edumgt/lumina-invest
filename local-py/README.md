# local-py — 로컬 전용 자동매매

이 레포 `app/` 의 자동매매(퀀트 사이클) 로직만 떼어내, **PostgreSQL · Redis · Celery · FastAPI 없이**
노트북에서 바로 돌아가는 Python 패키지로 옮긴 것이다. 외부 패키지도 쓰지 않는다(표준 라이브러리만).

```bash
cd local-py
python3 trade.py signals --source mock      # 오프라인 더미 시세로 지표·시그널 확인
python3 trade.py signals                    # 실제 시세(Yahoo)로 확인
python3 trade.py cycle --dry-run            # 한 사이클 판단 + 체결 시뮬(장부 안 바뀜)
python3 trade.py cycle                      # 한 사이클 실행(가상 장부 체결)
python3 trade.py loop --market-hours        # 3분 주기 루프, 정규장에만
python3 trade.py status                     # 계좌·보유·위험관리 상태
```

요구사항: Python 3.11 이상. 설치·가상환경·DB 준비 전부 불필요.

## 처음 쓰는 사람을 위한 단계별 가이드

### 1단계 — 네트워크 없이 동작 확인 (mock)

실제 시세도, 키도 없이 전체 경로가 도는지 먼저 본다. `mock` 은 종목코드로 시드를 고정한
랜덤워크라 같은 종목은 항상 같은 시계열이 나오므로, 결과를 비교하며 익히기에 좋다.

```bash
cd local-py
python3 trade.py signals --source mock      # 31종목 점수·판단 표
python3 trade.py cycle   --source mock      # 한 사이클 체결(가상 장부)
python3 trade.py status  --source mock      # 계좌·보유·위험관리
python3 trade.py orders                     # 주문 기록
python3 trade.py reset --yes                # 연습한 장부 비우기
```

### 2단계 — 실제 시세로 판단만 보기 (Yahoo, 주문 없음)

키 없이 바로 된다. `signals` 는 주문을 내지 않으므로 아무 때나 안전하다.

```bash
python3 trade.py signals                    # `*` 표시가 이번 사이클 대상 종목
python3 trade.py cycle --dry-run            # 주문까지 시뮬(장부 안 바뀜)
```

### 3단계 — 가상 장부로 모의 운용 (paper)

`paper` 는 실제 주문을 내지 않고 `data/state.json` 의 가상 계좌만 움직인다.
위험관리 다섯 가지는 이때도 모두 걸린다.

```bash
python3 trade.py cycle                                   # 1회
python3 trade.py loop --market-hours                     # 3분 주기, 정규장에만
python3 trade.py loop --interval 60 --max-cycles 10      # 1분 주기로 10회만
```

운용 설정을 바꿔 보려면 `.env.example` 을 `.env` 로 복사해 고치거나, CLI 로 그때그때 덮어쓴다.

```bash
cp .env.example .env
python3 trade.py cycle --top-n 5 --budget 500000 --strategy ma-cross-basic
python3 trade.py cycle --symbols 005930.KS 000660.KS     # 종목 직접 지정
python3 trade.py cycle --aggressive                      # 분봉 단기 모드
```

### 4단계 — 전략 비교와 백테스트

전략 스펙을 바꿔 가며 과거 성과를 먼저 본다(종가 체결 가정, 왕복 비용 0.25% 반영).

```bash
python3 trade.py backtest 005930.KS --period 5y
python3 trade.py backtest 005930.KS --strategy ma-cross-basic
python3 trade.py backtest 005930.KS --strategy momentum-breakout --fee-bps 30
python3 trade.py backtest                                # 유니버스 상위 5종목
```

`수익 N% (매수후보유 M%)` 로 같이 찍히므로 전략이 단순 보유를 이겼는지 바로 비교된다.

### 5단계 — KIS 실주문 (live)

**모의투자 키로 먼저** 확인한다. [KIS 개발자센터](https://apiportal.koreainvestment.com/)에서
모의투자 앱키·시크릿과 계좌번호를 받아 `.env` 에 넣는다.

```bash
# .env
QUANT_MODE=live
KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_ACCOUNT_NO=5012345601     # 8자리 계좌 + 2자리 상품코드
KIS_PAPER=true                # 모의투자 서버
```

```bash
python3 trade.py config                      # 키가 잡혔는지 확인(*** 로 가려서 출력)
python3 trade.py cycle --mode live           # 정규장 안에서 1회
```

키를 넣지 않고 `--mode live` 를 주면 MOCK 브로커가 주문 로그만 남기므로, 주문 경로를
자금 없이 점검할 수 있다. 실전 전환(`KIS_PAPER=false`)은 모의에서 며칠 돌려 본 뒤에 한다.

### 6단계 — 상시 운용

```bash
# 장중에만 3분 주기로 돌리고 로그를 파일에 남긴다
nohup python3 trade.py loop --market-hours >> data/loop.log 2>&1 &

python3 trade.py status                      # 수시 점검
python3 trade.py kill "점검 중"               # 즉시 정지(다음 사이클부터 주문 없음)
python3 trade.py resume                      # 해제
```

cron 으로 돌리려면 `loop` 대신 `cycle` 을 쓴다 — 상태가 파일에 있으니 프로세스가 끊겨도 이어진다.

```cron
*/3 9-15 * * 1-5 cd /home/ubuntu/lumina-invest/local-py && /usr/bin/python3 trade.py cycle >> data/cron.log 2>&1
```

## 출력 읽는 법

`signals` 표는 점수 내림차순이고 `*` 가 이번 사이클 거래 대상이다.

```
대상     점수 종목                             현재가 판단       근거
 *      2 161890.KS 한국콜마           170,871 매수       RSI 과매도 (매수 신호), 볼린저 하단 이탈 (반등 가능)
```

점수는 RSI(±2) + 이동평균 교차(±3)/배열(±1) + 볼린저(±1) 의 합(대략 -8~+8)이고,
`±3` 이상이면 강력 매수/매도, `±1` 이상이면 매수/매도, 그 사이는 관망이다.
전략 스펙을 쓰면 이 점수를 `[-1,1]` 로 정규화해 임계값으로 다시 판정한다.

`cycle` 한 줄 요약과 체결 목록:

```
2026-10-08 07:31:23 | 판단 3종목 | 체결 3건 / 생략 0건 | 총자산 10,481,663원 (4.82%)
    buy   000660.KS SK하이닉스         8주 @   116,107  [paper] RSI 중립 (53.0) | 단기 이평 > 중기 이평
    [생략] buy 042700.KS — 중복 주문 방지: 30분 내 동일 종목·방향 주문 존재
```

`[생략]` 은 위험관리가 막은 주문이다. 사유가 그대로 찍히므로 한도를 조정할지 판단할 수 있다.
사이클 전체 기록(판단 근거·데이터 출처 포함)은 `python3 trade.py cycle --json` 이나
`data/state.json` 의 `cycles` 에서 볼 수 있다(최근 50회).

## 문제 해결

| 증상 | 원인·해결 |
|---|---|
| `지표 계산 실패` 가 여러 종목에서 난다 | Yahoo 호출 실패. 잠시 후 재시도하거나 `--source mock` 으로 로직만 확인. 캐시가 있으면 그 값을 쓴다 |
| 체결이 한 건도 없다 | 대개 쿨다운이다. `status` 의 `주문 N건`·`쿨다운 M분` 확인. 연습 중이면 `RISK_COOLDOWN_MIN=0` |
| `데이터 부족` | 캔들 20개 미만. `QUANT_CANDLE_PERIOD` 를 `1y` 이상으로 |
| 비상 정지가 걸렸다 | 일손실 한도 초과. `status` 의 `halt_reason` 확인 후 `resume` |
| 실주문이 `market_closed` 로 생략된다 | 정규장 밖. 검증 목적이면 `KIS_ENFORCE_MARKET_HOURS=false` (휴장일엔 브로커가 거부) |
| `KIS 토큰 발급 실패` | 키·시크릿 오타, 또는 `KIS_PAPER` 와 키 종류(모의/실전) 불일치. `data/kis_token.json` 을 지우고 재시도 |
| `KIS 주문 거부 [...]` | 메시지가 KIS 원문 그대로다. 예수금·종목코드·호가단위·장운영 여부를 확인 |
| 시세가 갱신되지 않는다 | 일봉 캐시(기본 6시간). `QUANT_CANDLE_CACHE_HOURS=0` 이거나 `data/cache/` 삭제 |
| 장부를 처음부터 다시 | `python3 trade.py reset --yes --capital 20000000` |


## 무엇이 그대로이고 무엇이 바뀌었나

| 기능 | app/ (서버) | local-py |
|---|---|---|
| 지표·시그널 | `services/stock.py` | `indicators.py` — **계산식·점수·문구 동일** |
| 전략 스펙 | domain-rag-lab API (`strategy_loader`) | `strategy.py` + `strategies/*.json` |
| 공격 모드 | `services/aggressive_mode.py` | `aggressive.py` — **규칙 동일** |
| 위험관리 | `services/risk_guard.py` + Redis | `risk_guard.py` + `data/state.json` |
| 가상 계좌·주문 | PostgreSQL(`QuantVirtualAccount`·`Portfolio`·`Order`) | `store.py` → `data/state.json` |
| 사이클 실행 | Celery Beat (`quant.auto_trade_cycle`) | `trade.py loop` 의 `time.sleep` 루프 |
| 실주문 | stock-coin-trade 게이트웨이 / Secrets Manager | `broker.py` — KIS Open API 직접 호출 |
| 알림·감사로그 | Slack·메일·`audit` 테이블 | 콘솔 로그 |
| ML 점수 | SageMaker 배치 예측 | 없음(지표·규칙만). 스펙의 `lightgbm` 가중치는 무시된다 |

판단 경로(지표 → 시그널 → 전략 스펙 → 공격 모드 계획 → 위험관리 게이트 → 체결)와 그 순서는
서버와 같게 유지했으므로, 같은 시세를 주면 같은 결론이 나온다.

## 파일

```
local-py/
├── trade.py              # CLI 진입점
├── .env.example          # 설정 샘플 (.env 로 복사)
├── strategies/*.json     # 전략 스펙 3종(이동평균 교차 · 돌파 · 점수 임계값)
├── tests/test_local.py   # 자가 점검 32개 (네트워크 없음)
└── lumina_local/
    ├── config.py         # .env/CLI 설정
    ├── universe.py       # 31종목 유니버스(반도체·IT·K뷰티)
    ├── market_data.py    # 시세: yahoo | kis | mock + 파일 캐시
    ├── indicators.py     # RSI·이동평균·볼린저 + 규칙 기반 시그널
    ├── strategy.py       # 전략 스펙 평가(진입/청산 규칙, 임계값)
    ├── aggressive.py     # 분봉 단기 시그널 + 사이클 매수/매도 계획
    ├── risk_guard.py     # 쿨다운·일주문수·종목비중·일손실·비상정지
    ├── store.py          # 가상 계좌 장부(JSON)
    ├── broker.py         # KIS Open API(토큰·현재가·일봉·잔고·주문)
    ├── auto_trade.py     # 사이클 오케스트레이션 + 루프
    ├── backtest.py       # 단일 종목 백테스트
    └── cli.py            # 명령 정의
```

상태는 모두 `data/` 에 쌓인다 — `state.json`(장부·위험관리), `cache/`(시세), `kis_token.json`(접근토큰).
`.gitignore` 로 커밋에서 제외된다. 장부를 비우려면 `python3 trade.py reset`.

## 명령

| 명령 | 설명 |
|---|---|
| `signals` | 유니버스 31종목의 지표·점수·판단을 표로 출력(주문 없음). `*` 가 이번 사이클 대상 |
| `cycle` | 한 사이클 실행. `--dry-run` 은 장부를 바꾸지 않고, `--json` 은 사이클 로그 전체를 출력 |
| `loop` | 주기 실행. `--interval 60`, `--max-cycles 10`, `--market-hours` |
| `status` | 원금·총자산·수익률·보유종목·위험관리 상태 |
| `orders` | 최근 주문 기록(`--limit`) |
| `backtest` | 단일 종목 백테스트. `python3 trade.py backtest 005930.KS --period 5y` |
| `kill` / `resume` | 비상 정지 / 해제 |
| `reset` | 장부 초기화(`--yes`, `--capital`) |
| `config` | 현재 설정과 사용 가능한 전략 확인 |

공통 옵션: `--mode`, `--source`, `--strategy`, `--symbols`, `--top-n`, `--budget`,
`--buy-ratio`, `--sell-ratio`, `--aggressive`, `--user`.

## 시세 소스

* `yahoo`(기본) — 키가 필요 없다. 국내 **분봉은 약 20분 지연**이므로 공격 모드는 체결가와 벌어질 수 있다.
* `kis` — KIS Open API 로 일봉·현재가를 받는다(`KIS_APP_KEY`/`KIS_APP_SECRET` 필요). 분봉은 KIS 가
  1분봉 30개만 주어 5분봉 MA20 계산에 부족해, 분봉만 Yahoo 로 폴백한다.
* `mock` — 종목코드로 시드를 고정한 랜덤워크. 네트워크 없이 전체 경로를 점검할 때 쓴다.

일봉은 `data/cache/` 에 6시간(설정값) 캐시되므로 31종목 반복 스캔에도 호출이 몰리지 않는다.

## 전략 스펙

`strategies/<id>.json` 을 `--strategy <id>` 로 지정하면 지표 점수를 `[-1,1]` 로 정규화해
`buy_threshold`/`sell_threshold` 로 다시 판정한다. `entry`/`exit` 규칙(`ma_cross`, `momentum`,
`always`, `never`)이 평가 가능하면 **규칙이 임계값보다 우선**한다(청산 우선). 서버와 같은 스키마다.

```json
{
  "strategy_id": "ma-cross-basic", "version": 1,
  "universe": ["005930", "000660"],
  "position_sizing": {"max_symbols": 3},
  "signal_weights": {"buy_threshold": 0.25, "sell_threshold": -0.25, "technical": 1.0, "lightgbm": 0.0},
  "entry": {"indicator": "ma_cross", "condition": "short_above_long", "params": {"short_window": 5, "long_window": 20}},
  "exit":  {"indicator": "ma_cross", "condition": "short_below_long", "params": {"short_window": 5, "long_window": 20}}
}
```

## 위험관리

`paper` 모드에서도 서버와 같은 다섯 가지 안전장치가 모두 동작한다.

1. **중복 주문 방지** — 같은 종목·방향을 쿨다운(기본 30분, 공격 모드 3분) 안에 다시 주문하지 않는다
2. **일 주문 수 한도** — KST 날짜 기준(기본 20건, 공격 모드 300건)
3. **종목 비중 한도** — 매수 후 한 종목이 총자산의 30% 를 넘지 않도록 수량을 줄이거나 생략
4. **일손실 한도** — 당일 시작자산 대비 -3% 를 넘으면 비상 정지 + 루프 종료
5. **비상 정지** — 켜져 있으면 사이클이 주문을 전혀 내지 않는다(`resume` 으로 해제)

## 실주문(live) 주의

`--mode live` 는 가상 장부에 기록하는 **동시에** KIS 로 실제 주문을 보낸다.

1. 먼저 모의투자 키로 `KIS_PAPER=true` 상태에서 확인한다(기본값).
2. 키가 없으면 실주문 대신 MOCK 브로커가 로그만 남기므로 경로 점검에 쓸 수 있다.
3. `KIS_ENFORCE_MARKET_HOURS=true`(기본) 면 정규장(평일 09:00~15:30 KST) 밖에서는 실주문을 보내지 않는다.
   휴장일은 보지 않으므로 그때는 브로커가 거부한다.
4. 주문 유형은 지정가(`LIMIT`)이고, 공격 모드에서는 체결 우선으로 시장가(`MARKET`)로 보낸다.
5. 실주문이 실패해도 사이클은 멈추지 않고 로그와 사이클 기록에 남는다 —
   가상 장부와 실계좌가 어긋날 수 있으니 `status` 와 증권사 앱을 함께 확인한다.

`KIS_PAPER=false` 는 실제 자금이 집행된다. 이 코드는 교육·연구용이며 투자 권유가 아니다.

## 테스트

```bash
python3 -m unittest discover -s tests -v    # 32개, 네트워크 없이 1초대
```

지표 계산, 장부 체결(잔고 부족·보유 없음·평단 갱신), 위험관리 5종, 전략 스펙 판정,
공격 모드 익절·손절·로테이션, 사이클 전체(비상정지·일손실·한도·live 주문·장외 생략), 백테스트를 덮는다.
