#!/usr/bin/env python3
"""로컬 자동매매 실행 진입점 — `python trade.py <명령>`.

이 디렉터리(local-py)는 app/ 과 완전히 분리되어 있다. 표준 라이브러리만 쓰므로
가상환경·패키지 설치·DB·Redis 없이 Python 3.11+ 만 있으면 바로 돌아간다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lumina_local.cli import main   # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
