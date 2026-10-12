#!/usr/bin/env python3
"""4개 사이트 CSS 감사 — 2026-10-08 사고(주석 삽입으로 '/*' 한 줄이 사라져 :root 블록 전체가
무효화된 사고) 유형을 잡는다. 로컬 수정 직후와 배포 직후 두 번 돌린다.

  사용법:
    python3 shared-css/audit.py                      # 로컬 4개 저장소 전부
    python3 shared-css/audit.py --live               # 라이브 4개 사이트
    python3 shared-css/audit.py <파일|디렉터리> ...   # 지정 대상

검사 항목
  1. 주석 균형      닫히지 않은 /* , 짝 없는 */        ← 이번 사고
  2. 중괄호 균형    선언 블록이 열린 채 끝나는지
  3. :root 토큰 수  주 CSS 가 최소 기대치를 넘는지     ← 사고 시 0 이 된다
  4. 표준 토큰      8개 공통 토큰 선언 여부
  5. 미정의 var()   주 CSS + common.css 어디에도 없는 토큰
  6. 18px 초과      공통 타이포그래피 가이드 2·3항
종료 코드: 오류 1건 이상이면 1
"""
import re, sys, os, subprocess

ROOT = "/home/ubuntu"
COMMON_URL = "https://www.edumgt.co.kr/css/common.css"
COMMON_SRC = f"{ROOT}/lumina-invest/shared-css/common.css"

# 사이트별 주 CSS 와 :root 토큰 최소 기대치(사고 탐지용 하한)
MAIN = {
    "pr": (f"{ROOT}/domain-rag-lab/frontend/style.css",        "https://pr.edumgt.co.kr/static/style.css",   15),
    "fd": (f"{ROOT}/lumina-invest/public/css/app.css",         "https://fd.edumgt.co.kr/css/app.css",        20),
    "st": (f"{ROOT}/stock-coin-trade/frontend/css/style.css",   "https://st.edumgt.co.kr/css/style.css",      20),
    "iv": (f"{ROOT}/stock-kms-portal/frontend/style.css",       "https://iv.edumgt.co.kr/static/style.css",   15),
}
STD_TOKENS = ["--bg","--surface","--border","--text","--muted","--accent","--radius","--radius-sm"]

errors, warns = [], []
def err(m): errors.append(m); print(f"  \033[31m오류\033[0m  {m}")
def warn(m): warns.append(m); print(f"  경고  {m}")
def ok(m):   print(f"  정상  {m}")

def fetch(url):
    import urllib.request
    with urllib.request.urlopen(url, timeout=20) as r:
        return r.read().decode("utf-8", "replace")

def comment_balance(css):
    depth = stray = i = 0
    while i < len(css):
        if css.startswith("/*", i): depth += 1; i += 2; continue
        if css.startswith("*/", i):
            if depth: depth -= 1
            else: stray += 1
            i += 2; continue
        i += 1
    return stray, depth

def brace_balance(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return css.count("{") - css.count("}")

def root_tokens(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    n = set()
    for m in re.finditer(r":root[^{}]*\{([^{}]*)\}", css):
        n |= set(re.findall(r"(--[\w-]+)\s*:", m.group(1)))
    return n

def declared(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return set(re.findall(r"(--[\w-]+)\s*:", css))

def used(css):
    """var(--x) 중 대체값이 없는 것만 — var(--x, 기본) 은 미정의라도 안전하므로 뺀다."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return set(re.findall(r"var\(\s*(--[\w-]+)\s*\)", css))

def big_fonts(css):
    out = []
    for m in re.finditer(r"font-size\s*:\s*([\d.]+)px", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        if float(m.group(1)) > 18: out.append(m.group(1) + "px")
    return out

def check(label, css, min_tokens=None, common_css=""):
    print(f"\n── {label}  ({len(css)}B)")
    stray, unclosed = comment_balance(css)
    if stray or unclosed: err(f"주석 불균형: 짝 없는 '*/' {stray}개, 닫히지 않은 '/*' {unclosed}개")
    else: ok("주석 균형")
    b = brace_balance(css)
    if b: err(f"중괄호 불균형: {b:+d}")
    else: ok("중괄호 균형")
    if min_tokens is not None:
        t = root_tokens(css)
        if len(t) < min_tokens: err(f":root 토큰 {len(t)}개 — 기대 최소 {min_tokens}개 (블록이 삼켜졌을 수 있다)")
        else: ok(f":root 토큰 {len(t)}개")
        miss = [x for x in STD_TOKENS if x not in declared(css) and x not in declared(common_css)]
        if miss: warn(f"표준 토큰 미선언: {' '.join(miss)}")
        else: ok("표준 토큰 8개 모두 선언")
    undef = sorted(used(css) - declared(css) - declared(common_css))
    if undef: warn(f"미정의 var(): {' '.join(undef[:8])}{' …' if len(undef) > 8 else ''} ({len(undef)}개)")
    else: ok("미정의 var() 없음")
    big = big_fonts(css)
    if big: warn(f"18px 초과 font-size {len(big)}건: {' '.join(sorted(set(big))[:6])}")
    else: ok("font-size 18px 이하")

def main():
    args = sys.argv[1:]
    live = "--live" in args
    args = [a for a in args if not a.startswith("--")]
    print("=" * 72)
    print("공통 CSS 감사 — " + ("라이브" if live else "로컬"))
    print("=" * 72)
    common = fetch(COMMON_URL) if live else open(COMMON_SRC, encoding="utf-8").read()
    check("common.css" + (" (라이브 S3)" if live else " (소스)"), common)
    if args:
        for a in args:
            check(a, open(a, encoding="utf-8", errors="replace").read(), common_css=common)
    else:
        for site, (path, url, mn) in MAIN.items():
            try:
                css = fetch(url) if live else open(path, encoding="utf-8", errors="replace").read()
            except Exception as e:
                err(f"{site}: 읽기 실패 {e}"); continue
            check(f"{site}  {url if live else path}", css, mn, common)
    print("\n" + "=" * 72)
    print(f"오류 {len(errors)}건, 경고 {len(warns)}건")
    return 1 if errors else 0

if __name__ == "__main__":
    sys.exit(main())
