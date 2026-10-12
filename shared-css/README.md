# 공통 CSS (주식투자 4개 사이트)

`common.css` 는 pr(domain-rag-lab) · fd(lumina-invest) · st(stock-coin-trade) · iv(stock-kms-portal)
네 사이트가 **함께 쓰는 단 하나의 스타일 정본**이다.

- 런타임 정본: `https://www.edumgt.co.kr/css/common.css` (S3 + CloudFront)
- 소스 정본: 이 디렉터리의 `common.css` (버전 관리용. 각 사이트가 런타임에 읽지는 않는다)
- 배포: `./shared-css/deploy.sh`

## 두 가지 성질을 구분해서 담는다

이 파일은 **모든 HTML 에서 마지막 스타일시트**로 불린다. 그런데 담긴 블록은 성질이 정반대인 두 종류다.

| 성질 | 블록 | 사이트 CSS 와의 우선순위 |
| --- | --- | --- |
| **계층(@layer)** = 기본값 | `edumgt-reset` · `edumgt-tokens` · `edumgt-components` | **사이트가 이긴다.** 계층에 속한 규칙은 계층 밖 규칙보다 항상 약하다(CSS Cascade Layers). 사이트가 같은 셀렉터·토큰을 선언하면 사이트 값이 쓰이고, 선언이 없을 때만 공통 기본값이 보인다 |
| **계층 밖 `!important`** = 강제 규약 | 한글 글자 스케일 · 최소 글자 크기 · 타이틀 고정 · 공통 푸터 | **공통이 이긴다.** 사이트 CSS·인라인·유틸리티 클래스를 덮는다 |

새 규칙을 추가할 때 이 둘 중 어디에 속하는지 먼저 정한다. 섞으면 안 된다.

### 계층별 내용

| 계층 | 내용 |
| --- | --- |
| `edumgt-reset` | `box-sizing: border-box` 만. `margin/padding: 0` 은 pr·iv 에만 있던 것이라 fd·st 레이아웃을 바꾸므로 넣지 않는다 |
| `edumgt-tokens` | 표준 토큰 8개 + 스크롤바 토큰 6개의 **이름과 기본값** |
| `edumgt-components` | 스크롤바 4종, `.btn-primary`, `.btn-ghost`, `.eyebrow` — 토큰만 참조해서 작성 |

### 강제 규약 블록

| 블록 | 내용 |
| --- | --- |
| 한글 글자 스케일 | `Pretendard Hangul 110` @font-face, 한글 글리프만 `size-adjust:110%` |
| 전역 최소 글자 크기 | `small`·`sub`·`sup` 을 최소 13px 로 바닥 고정 |
| 타이틀 고정 | `--title-size:18px`, `h1`·`h2`·`.page-title` 을 18px Pretendard 로 `!important` 고정 |
| 공통 푸터 | 높이 25px·검정 배경·중앙 정렬 1줄 푸터 |

## 표준 토큰 계약

공통 컴포넌트는 아래 8개 이름만 참조한다. **값은 각 사이트가 공급**한다(각 저장소 주 CSS 끝의 `표준 토큰 계약` 블록).

| 토큰 | 뜻 | 토큰 | 뜻 |
| --- | --- | --- | --- |
| `--bg` | 페이지 배경 | `--text` | 본문 글자색 |
| `--surface` | 카드·패널 배경 | `--muted` | 보조 글자색 |
| `--border` | 선·테두리 | `--accent` | 강조색 |
| `--radius` | 기본 모서리 | `--radius-sm` | 작은 모서리 |

추가로 `--accent-on`(강조색 위 글자색)과 스크롤바 토큰 `--sb-width`·`--sb-height`·`--sb-track`·`--sb-thumb`·`--sb-radius`·`--sb-thumb-hover` 를 쓴다.

사이트별 매핑 현황(2026-10-12):

| 사이트 | 자체 이름 → 표준 이름 |
| --- | --- |
| pr | `--text-muted` → `--muted`, `--accent-on: #000` |
| fd | `--surf` → `--surface`, `--text-mute` → `--muted`, `--radius`·`--radius-sm` 신규 |
| st | `--fg` → `--text` |
| iv | `--text-muted` → `--muted`, `--accent-on: #000` |

## 담지 않는 것

테마 토큰의 **값**(색·배경·그림자·레이아웃)은 사이트마다 다르므로 각 저장소의 주 CSS 에 남겨 둔다.

- pr: 다크 + 노란색 악센트 · fd: 라이트 · st: 라이트 TradingView 블루 · iv: 파랑/노랑

측정 결과 4개 저장소에 **완전히 동일한 규칙은 0개**이고(2026-10-12, 규칙 13,000여 개 비교),
pr↔iv 포크쌍만 3,419개·337KB 를 공유한다. 그래서 공통화는 "규칙 옮기기"가 아니라
**토큰 이름 통일 + 토큰만 참조하는 구조 계층**으로 한다. pr↔iv 포크 중복 정리는 별건이다.

## 감사 스크립트

```bash
python3 shared-css/audit.py          # 로컬 4개 저장소
python3 shared-css/audit.py --live   # 라이브 4개 사이트
```

2026-10-08 에 주석 3줄을 삽입하다 `/*` 한 줄이 사라져 `:root` 블록 전체가 셀렉터로 삼켜지고
fd·st 사이트 테마가 통째로 날아간 사고가 있었다. 1번 검사(주석 균형)와 3번 검사(:root 토큰 수)가
그 유형을 잡는다. **CSS 를 고친 직후와 배포 직후 두 번 돌린다.** `deploy.sh` 는 업로드 전에 자동으로 돌린다.

## 각 사이트에서 읽는 방법

모든 HTML 의 `</head>` 바로 앞에서 **마지막 스타일시트**로 불러온다. 순서가 중요하다 —
공통 블록은 각 사이트 CSS 를 덮어써야 하므로 반드시 뒤에 와야 한다.

```html
<link rel="stylesheet" href="https://www.edumgt.co.kr/css/common.css?v=20261008-common-1" />
</head>
```

## 주의

- 이 블록들을 각 저장소 CSS 에 **다시 복제하지 말 것**. 2026-10-08 개편 전에는 9개 파일에 복제돼 있었다.
- 예외 하나: `stock-coin-trade/vscode-kis-mcp/media/panel.css` 는 VSCode 웹뷰이고
  CSP 가 `style-src ${webview.cspSource}` 라서 외부 CSS 를 못 읽는다. 그 파일은 공통 푸터 블록을 로컬에 유지한다.
