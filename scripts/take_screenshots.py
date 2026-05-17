"""
Lumina Invest — 스크린샷 자동 캡처 (GNB/LNB 버튼 클릭 방식)
Usage: .venv/bin/python scripts/take_screenshots.py
"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE  = "http://localhost:8000"
EMAIL = "test@test.com"
PASS  = "test1234"
OUT   = Path(__file__).parent.parent / "screenshots"
OUT.mkdir(exist_ok=True)

W, H = 1440, 900

# (파일명, GNB data-gnb, LNB data-view)
SHOTS = [
    ("cap01_agent_home",   "agent",   "agent-chat"),
    ("cap03_app_main",     "agent",   "agent-news"),
    ("cap02_quant",        "quant",   "quant-dashboard"),
    ("cap04_quant",        "quant",   "quant-backtest"),
    ("cap03_us_stocks",    "us",      "us-dashboard"),
    ("cap04_company",      "company", "company-dashboard"),
    ("cap05_company",      "company", "company-compare"),
    ("cap05_trading",      "trading", "trading-chart"),
    ("final01_agent",      "agent",   "agent-chat"),
    ("final02_stock_apex", "trading", "trading-chart"),
    ("final03_quant_apex", "quant",   "quant-backtest"),
    ("final04_us_apex",    "us",      "us-chart"),
    ("final05_company",    "company", "company-dashboard"),
]

def login(page):
    page.goto(BASE + "/login.html", wait_until="networkidle")
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", PASS)
    page.click("button[type=submit]")
    page.wait_for_url("**/app.html**", timeout=12_000)
    time.sleep(2)

def nav_to(page, gnb_key, view_key):
    # GNB 탭 클릭
    page.click(f"[data-gnb='{gnb_key}']", timeout=5000)
    time.sleep(0.6)
    # LNB 항목 클릭
    page.click(f"[data-view='{view_key}']", timeout=5000)
    time.sleep(1.5)

def shot(page, name):
    out = OUT / f"{name}.png"
    page.screenshot(path=str(out))
    print(f"  ✓ {out.name}")

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": W, "height": H})
        page = ctx.new_page()

        # ── 로그인/회원가입 페이지 ──────────────────────────
        print("[1] 인증 페이지")
        page.goto(BASE + "/login.html", wait_until="networkidle")
        time.sleep(0.8)
        shot(page, "cap01_login")

        page.goto(BASE + "/register.html", wait_until="networkidle")
        time.sleep(0.8)
        shot(page, "cap02_register")

        # ── 로그인 후 앱 ────────────────────────────────────
        print("[2] 앱 뷰")
        login(page)

        prev_gnb = None
        for name, gnb, view in SHOTS:
            try:
                # 같은 GNB면 LNB만 클릭
                if gnb != prev_gnb:
                    page.click(f"[data-gnb='{gnb}']", timeout=5000)
                    time.sleep(0.8)
                    prev_gnb = gnb
                page.click(f"#lnb-nav [data-view='{view}']", timeout=5000)
                time.sleep(1.5)
                shot(page, name)
            except Exception as e:
                print(f"  ✗ {name}: {e}")

        browser.close()
    print(f"\n완료: {OUT}")

if __name__ == "__main__":
    main()
