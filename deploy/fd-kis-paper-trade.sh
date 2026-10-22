#!/usr/bin/env bash
# KIS 모의계좌 목표 보유수량 맞추기(현대차 8 · 삼성전자 86 · HD현대중공업 1 · 한미약품 2). lumina-invest 루트에서 사용자가 직접 실행.
# fd fin-ai-app 컨테이너 안에서 deploy/fd_kis_paper_trade.py 를 돌린다(화면 수동 주문과 같은 경로 → live_orders 추적·알림·감사 로그).
#   bash deploy/fd-kis-paper-trade.sh            # dry-run: 잔고·보유·현재가·주문 계획만 출력
#   bash deploy/fd-kis-paper-trade.sh --execute  # 주문 실행 (장 운영시간 09:00~15:30 KST 안에서)
#   bash deploy/fd-kis-paper-trade.sh --execute --add   # '현재 보유 + 수량' 으로 추가 매수
# 비상 정지 상태면 전부 거절되므로 먼저 kis-monitor 화면에서 해제한다.
set -euo pipefail
cd "$(dirname "$0")/.."
K=fd.edumgt.co.kr.pem; H=ubuntu@43.201.229.188
ARGS="$*"
ssh -i "$K" -o StrictHostKeyChecking=no "$H" "sudo -n docker exec -i fin-ai-app python - $ARGS" < deploy/fd_kis_paper_trade.py
