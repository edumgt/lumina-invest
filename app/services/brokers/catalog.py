"""지원 증권사 카탈로그."""

from copy import deepcopy

_BROKER_CATALOG = [
    {
        "code": "kis",
        "name": "한국투자증권",
        "api_type": "REST + WebSocket",
        "assets": "국내·해외주식, 채권, 선물옵션",
        "notes": "국내 유일 REST API, OS 제약 없음, Python 샘플 제공",
        "windows_only": False,
        "status": "available",
    },
    {
        "code": "kiwoom",
        "name": "키움증권",
        "api_type": "OCX (Windows 전용)",
        "assets": "국내주식 중심",
        "notes": "가장 오래된 API, 커뮤니티 자료 풍부",
        "windows_only": True,
        "status": "available",
    },
    {
        "code": "daishin",
        "name": "대신증권",
        "api_type": "COM (Windows 전용)",
        "assets": "국내주식, 파생상품",
        "notes": "CYBOS Plus 기반, 백테스트 자료 많음",
        "windows_only": True,
        "status": "available",
    },
    {
        "code": "meritz",
        "name": "메리츠증권",
        "api_type": "REST (출시 예정)",
        "assets": "국내주식",
        "notes": "신규 API 준비 중, 무수수료 ‘슈퍼365’ 계좌와 결합 예정",
        "windows_only": False,
        "status": "planned",
    },
    {
        "code": "shinhan",
        "name": "신한투자증권",
        "api_type": "자동감시주문 시스템",
        "assets": "국내·해외주식",
        "notes": "조건 충족 시 자동 주문, 대량 주문 처리 기능",
        "windows_only": False,
        "status": "available",
    },
    {
        "code": "mock",
        "name": "Mockup",
        "api_type": "Simulated",
        "assets": "테스트용",
        "notes": "키/시크릿 미입력 시 자동 사용",
        "windows_only": False,
        "status": "available",
    },
]


def get_broker_catalog() -> list[dict]:
    return deepcopy(_BROKER_CATALOG)


def get_broker_codes() -> set[str]:
    return {b["code"] for b in _BROKER_CATALOG}

