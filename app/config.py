from pydantic_settings import BaseSettings
import os


class Settings(BaseSettings):
    PORT: int = 8000
    SESSION_SECRET: str = "change-me-super-secret"
    # 세션 유효 기간(초). 슬라이딩 만료: 마지막 요청 시각으로부터 SESSION_TTL 후 만료된다.
    SESSION_TTL: int = 2592000  # 30일
    # 슬라이딩 만료 갱신 최소 간격(초). 매 요청마다 Redis EXPIRE/Set-Cookie 를 보내지 않고,
    # 마지막 갱신 후 이 시간이 지난 요청에서만 TTL 과 브라우저 쿠키 만료를 함께 연장한다.
    SESSION_REFRESH_INTERVAL: int = 300  # 5분
    SESSION_COOKIE_NAME: str = "fin_session"

    REDIS_URL: str = "redis://localhost:6379"

    # ── PostgreSQL (SQLAlchemy async + asyncpg) ───────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://lumina:lumina@localhost:5432/lumina"

    # ── JWT ──────────────────────────────────────────────────────────────────
    JWT_SECRET: str = "change-me-jwt-secret-32chars-min!!"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TTL: int = 900       # 15분 (초)
    JWT_REFRESH_TTL: int = 604800   # 7일 (초)

    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    LLM_MODEL: str = "qwen2.5:1.5b"
    EMBED_MODEL: str = "nomic-embed-text"
    VLM_MODEL: str = "llava"          # Vision-Language Model for image/slide description
    OLLAMA_TIMEOUT: float = 300.0

    # ── LLM 서빙 백엔드 선택 (채팅/에이전트 전용, 임베딩은 항상 Ollama 사용) ─────
    # ollama(기본, 로컬/EC2 Ollama) | bedrock | sagemaker | vllm
    LLM_PROVIDER: str = "ollama"

    # Bedrock (converse API 사용, AWS_REGION 재사용)
    BEDROCK_MODEL_ID: str = ""  # 예: us-east-1의 meta.llama3-1-8b-instruct-v1:0

    # SageMaker JumpStart 엔드포인트 (invoke_endpoint)
    SAGEMAKER_ENDPOINT_NAME: str = ""

    # EC2/ECS 위의 vLLM (OpenAI 호환 /v1/chat/completions)
    VLLM_BASE_URL: str = ""
    VLLM_MODEL: str = ""

    # OpenAI API (채팅 화면에서 "OpenAI API Key 입력" 모드 선택 시 사용)
    # 키는 브라우저가 요청마다 보내며 서버에 저장하지 않는다. OPENAI_API_KEY 는 요청에 키가 없을 때의 서버 기본값(선택).
    OPENAI_BASE_URL: str = "https://api.openai.com"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_API_KEY: str = ""

    VECTOR_STORE: str = "qdrant"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "fin_chunks"
    DOCUMENT_COLLECTION: str = "fin_chunks"  # Qdrant collection for uploaded documents

    DATA_DIR: str = "./data"
    TOP_K: int = 6

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "finagent123"

    # ── AWS (Comprehend 감성분석 / SageMaker 배치 학습 결과 조회) ──────────────
    AWS_REGION: str = "ap-northeast-2"  # 실제 운영 EC2(fund-web)가 있는 리전
    ML_ARTIFACTS_BUCKET: str = ""  # SageMaker 학습 산출물(scores.json)이 저장된 S3 버킷

    # ── QuantConnect LEAN 백테스트 (domain-rag-lab / stock-coin-trade 이식) ──────
    # auto | ssh | docker | local  (auto: SSH 설정 있으면 ssh → docker CLI 있으면 docker → local)
    LEAN_MODE: str = "auto"
    LEAN_DOCKER_IMAGE: str = "quantconnect/lean:latest"
    LEAN_TIMEOUT_SECONDS: int = 300
    LEAN_KEEP_WORKDIR: bool = False           # 디버깅용: 실행 폴더(main.py/prices.csv/results) 보존
    # docker 모드: 이 프로세스가 쓰는 작업 폴더. 컨테이너 안에서 실행하면 같은 경로가 호스트에서
    # LEAN_HOST_WORKDIR로 보이도록 volume을 맞춰야 한다 (docker-compose.yml 참고).
    LEAN_WORKDIR: str = "./data/lean-workflows"
    LEAN_HOST_WORKDIR: str = ""
    # docker 모드에서 LEAN_WORKDIR 대신 named volume 이름을 마운트 (컨테이너 안에서 실행할 때 권장)
    LEAN_DOCKER_VOLUME: str = ""
    DOCKER_SOCK: str = "/var/run/docker.sock"
    # ssh 모드: 원격 LEAN 실행 서버 (PEM 키는 저장소에 넣지 말고 읽기 전용으로 마운트)
    LEAN_SSH_HOST: str = ""
    LEAN_SSH_USER: str = "ubuntu"
    LEAN_SSH_KEY_PATH: str = ""
    LEAN_REMOTE_WORKDIR: str = "/home/ubuntu/lean-workflows"

    # ── Alpaca Paper Trading (읽기 전용 연결 테스트 + 퀀트 파이프라인 주문) ─────
    ALPACA_API_KEY: str = ""
    ALPACA_SECRET_KEY: str = ""

    # ── KIS 자동매매 실주문 게이트웨이 (stock-coin-trade Open API, 계약: docs/contracts/kis-autotrade-api.md) ──
    # 비어 있으면 게이트웨이를 쓰지 않고 기존 KISClient 직접 호출(레거시)로 폴백한다.
    STOCK_COIN_TRADE_BASE_URL: str = ""            # 예: https://stock.example.com
    STOCK_COIN_TRADE_API_KEY: str = ""             # stock-coin-trade에서 발급한 Open API 키 (KIS_AUTOTRADE_API_KEY_IDS 등록 필요)
    STOCK_COIN_TRADE_TIMEOUT: float = 15.0
    # paper(KIS Testbed) | real(실전). quant_mode=live 인 사용자의 주문이 이 환경으로 나간다. Phase 4까지 paper 유지.
    STOCK_COIN_TRADE_KIS_ENVIRONMENT: str = "paper"
    STOCK_COIN_TRADE_ORDER_TYPE: str = "LIMIT"     # LIMIT(현재가 호가 보정) | MARKET
    STOCK_COIN_TRADE_ENFORCE_MARKET_HOURS: bool = True  # 평일 09:00~15:30 KST 외에는 실주문을 보내지 않는다 (가상계좌 체결은 영향 없음)
    STOCK_COIN_TRADE_CANCEL_OPEN_AFTER_MIN: int = 0     # N분 넘게 미체결(ACCEPTED/PARTIALLY_FILLED)이면 confirm_fills 가 취소 요청. 0=끔
    KRX_EXTRA_HOLIDAYS: str = ""                        # 추가 휴장일 (YYYY-MM-DD 쉼표 구분). 내장 2026 캘린더에 더해진다
    ML_SCORE_SCALE_PCT: float = 30.0                    # SageMaker 예측 연수익률(%)을 [-1,1]로 정규화할 때의 분모

    # ── KIS 모의투자(Testbed) 백그라운드 배치 (app/services/kis_batch.py) ──────────
    # true 면 celery-beat 의 quant.auto_trade_cycle 이 시스템 사용자(SYSTEM_USER_ID) 행을 만들어 자동매매를 켠다.
    # 사용자 로그인·대시보드 버튼이 필요 없다. 실주문 경로가 KIS paper(Testbed) 일 때만 켜지고 real 이면 켜지지 않는다.
    KIS_PAPER_BATCH_ENABLED: bool = False
    KIS_PAPER_BATCH_SYMBOLS: str = ""                   # 비우면 AI 추천 상위 N. "005930.KS,035720.KS" 처럼 주면 수동 종목
    KIS_PAPER_BATCH_AI_TOP_N: int = 3
    KIS_PAPER_BATCH_PER_TRADE_BUDGET: float = 300_000   # 1회 투자금(원). 나머지 한도는 kis_quickstart.TESTBED_DEFAULTS

    # ── KIS 자격증명 (서버 관리 — 사용자는 화면에서 입력하지 않는다, app/services/kis_credentials.py) ──
    # Secrets Manager 시크릿 이름/ARN. JSON: {"app_key","app_secret","account_no","environment": "paper|real"}
    KIS_SECRETS_NAME: str = ""                      # 예: lumina-invest/prod/kis
    KIS_SECRETS_CACHE_TTL: int = 600                # 초. 조회 성공값 캐시 (실패는 60초)
    KIS_ENVIRONMENT: str = "paper"                  # 시크릿에 environment 가 없을 때의 기본값. paper=Testbed, real=실전
    # Secrets Manager 를 못 쓰는 로컬 개발·장애 시 폴백 (운영에서는 비워 둔다)
    KIS_APP_KEY: str = ""
    KIS_APP_SECRET: str = ""
    KIS_ACCOUNT_NO: str = ""

    # ── 전략 스펙 API (domain-rag-lab /backtests/strategies) ───────────────────
    DOMAIN_RAG_LAB_BASE_URL: str = ""
    DOMAIN_RAG_LAB_API_KEY: str = ""
    STRATEGY_SPEC_CACHE_TTL: int = 600

    # ── 외부 Open API (/openapi/v1) 호출 제한 ────────────────────────────────
    OPENAPI_RATE_LIMIT_MAX: int = 60       # 키당 분당 호출 수
    OPENAPI_RATE_LIMIT_WINDOW: int = 60    # 초

    # ── TradingView Webhook 보호 ─────────────────────────────────────────────
    # TradingView 공식 알림 발신 IP (docs: Webhooks). 운영에서 TRADINGVIEW_ENFORCE_IP=true 로 켠다.
    TRADINGVIEW_ALLOWED_IPS: str = "52.89.214.238,34.212.75.30,54.218.53.128,52.32.178.7"
    TRADINGVIEW_ENFORCE_IP: bool = False
    TRADINGVIEW_RATE_LIMIT_MAX: int = 30    # 키당 분당 알림 수

    # ── 운영 ────────────────────────────────────────────────────────────────
    # 앱 기동 시 alembic upgrade head 실행 여부. 복제 인스턴스가 여러 개인 운영 환경에서는 false 로 두고
    # 배포 단계에서 scripts/migrate.sh 로 1회 실행한다.
    RUN_MIGRATIONS_ON_STARTUP: bool = True
    PUBLIC_BASE_URL: str = ""               # Webhook URL 안내 등에 쓰는 외부 공개 주소 (예: https://fund.example.com)

    ADMIN_EMAILS: str = ""
    TRUST_PROXY: bool = False
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"

    # ── 알림 채널 설정 ──────────────────────────────────────────────────────────

    # 텔레그램
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Slack Incoming Webhook
    SLACK_WEBHOOK_URL: str = ""

    # 이메일 (SMTP / STARTTLS)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_TO: str = ""

    # 카카오 알림톡 · SMS (CoolSMS REST API)
    COOLSMS_API_KEY: str = ""
    COOLSMS_API_SECRET: str = ""
    KAKAO_SENDER_KEY: str = ""   # 카카오 채널 발신 프로필 키
    KAKAO_PHONE: str = ""        # 수신 전화번호 (예: 01012345678)
    SMS_FROM: str = ""           # 발신 번호
    SMS_TO: str = ""             # 수신 번호

    class Config:
        env_file = os.getenv("ENV_FILE", ".env.dev")
        extra = "ignore"

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip() for e in self.ADMIN_EMAILS.split(",") if e.strip()]


settings = Settings()
