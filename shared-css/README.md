# 공통 CSS (주식투자 4개 사이트)

`common.css` 는 pr(domain-rag-lab) · fd(lumina-invest) · st(stock-coin-trade) · iv(stock-kms-portal)
네 사이트가 **함께 쓰는 단 하나의 스타일 정본**이다.

- 런타임 정본: `https://www.edumgt.co.kr/css/common.css` (S3 + CloudFront)
- 소스 정본: 이 디렉터리의 `common.css` (버전 관리용. 각 사이트가 런타임에 읽지는 않는다)
- 배포: `./shared-css/deploy.sh`

## 담고 있는 것

| 블록 | 내용 |
| --- | --- |
| 한글 글자 스케일 | `Pretendard Hangul 110` @font-face, 한글 글리프만 `size-adjust:110%` |
| 전역 최소 글자 크기 | `small`·`sub`·`sup` 을 최소 13px 로 바닥 고정 |
| 타이틀 고정 | `--title-size:18px`, `h1`·`h2`·`.page-title` 을 18px Pretendard 로 `!important` 고정 |
| 공통 푸터 | 높이 25px·검정 배경·중앙 정렬 1줄 푸터 |

## 담지 않는 것

테마 토큰(색·배경·그림자·레이아웃)은 사이트마다 다르므로 각 저장소의 주 CSS 에 남겨 둔다.

- pr: 다크 + 노란색 악센트 · fd: 라이트 · st: 라이트 TradingView 블루 · iv: 파랑/노랑

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
