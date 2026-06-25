# AWS Work

`lumina-invest` AWS 포팅 작업 폴더입니다. JSON 파일들은 **실제 리소스 값이 반영된 최종본**이므로 별도 sed 치환 없이 바로 사용 가능합니다.

---

## 현재 배포 상태 (2026-06-25 최종)

| 리소스 | ID / 값 | 상태 |
|--------|---------|------|
| VPC | `vpc-01879d70df92987b9` (final-vpc, 172.30.0.0/16) | ✅ |
| ECR | `086015456585.dkr.ecr.ap-northeast-2.amazonaws.com/lumina-invest:latest` | ✅ |
| ECS Cluster | `lumina-prod` | ✅ |
| ECS API Service | `lumina-invest-api-svc` — task-def rev 2, 2/2 running | ✅ |
| ECS Worker Service | `lumina-invest-worker-svc` — task-def rev 2, 1/1 running | ✅ |
| ALB | `lumina-alb` → `lumina-alb-1822566663.ap-northeast-2.elb.amazonaws.com` (HTTP/80) | ✅ |
| ALB Target | `172.30.139.251` (2a) / `172.30.37.124` (2b) — **healthy** | ✅ |
| DocumentDB | `lumina-docdb.cluster-cg0ugoglztrn.ap-northeast-2.docdb.amazonaws.com:27017` | ✅ |
| Secrets Manager | `lumina-invest/prod/app` (suffix: `-c4WE2y`) | ✅ |
| S3 Front Bucket | `lumina-invest-front-086015456585` | ✅ |
| CloudFront | `E2Z5V1W67DDW54` → `d3ls3wdarllhnf.cloudfront.net` | ✅ |
| OAC | `E2HVAGOCBF9LUY` (lumina-invest-oac) | ✅ |
| EC2 (Redis+Ollama+Neo4j+Qdrant) | `i-0af91b80c1ada35f3` (172.30.2.131, t3.medium) | ✅ |
| IAM Exec Role | `ecsTaskExecutionRole` + `SecretsManagerReadAccess` 인라인 정책 | ✅ |
| IAM Task Role | `luminaInvestTaskRole` | ✅ |
| IAM Scheduler Role | `luminaSchedulerRole` | ✅ |

### 남은 작업

| 항목 | 내용 |
|------|------|
| ⏳ DocDB 비밀번호 | Secrets Manager `MONGO_URI`의 `docdbuser` 비밀번호를 실제 값으로 교체 필요 |
| ⏳ EventBridge Scheduler | hourly/daily 스케줄 아직 미생성 |

---

## 아키텍처

```
사용자 (HTTPS)
    ↓
CloudFront  d3ls3wdarllhnf.cloudfront.net
    ├─ /*, /*.html  →  S3 lumina-invest-front-086015456585  (OAC: E2HVAGOCBF9LUY)
    └─ /api/*       →  ALB lumina-alb (HTTP/80)
                            ↓
                    ECS Fargate (lumina-prod)
                    assignPublicIp=ENABLED
                    subnet-0d28ba1be9a1ff04c (ap-northeast-2a)
                    subnet-0e0cd6292f59623af (ap-northeast-2b)
                      ├─ lumina-invest-api-svc    (2태스크, 1024CPU/2048MB, rev 2)
                      └─ lumina-invest-worker-svc (1태스크,  512CPU/1024MB, rev 2)
                            ↓
                    EC2 i-0af91b80c1ada35f3 (172.30.2.131)
                      ├─ Redis   :6379
                      ├─ Ollama  :11434
                      ├─ Neo4j   :7687
                      └─ Qdrant  :6333
                            ↓
                    DocumentDB lumina-docdb :27017 (TLS + RDS CA)
```

> **주의**: EC2 단일 장애점 — Redis·Ollama·Neo4j·Qdrant 4개 서비스가 `i-0af91b80c1ada35f3` 1대에서 동작 중

---

## 파일 목록

| 파일 | 설명 | 상태 |
|------|------|------|
| `api-taskdef.json` | ECS API 태스크 정의 rev 2 (실제 값 반영) | ✅ 등록됨 |
| `worker-taskdef.json` | ECS Worker 태스크 정의 rev 2 (실제 값 반영) | ✅ 등록됨 |
| `scheduler-ecs-policy.json` | Scheduler → ECS 실행 권한 (실제 값 반영) | ⏳ Role 적용 필요 |
| `cf-distribution.json` | CloudFront 배포 설정 (참조용, 이미 배포됨) | ✅ 참조용 |
| `ecs-tasks-trust-policy.json` | ECS 태스크 신뢰 정책 | ✅ |
| `scheduler-trust.json` | Scheduler 신뢰 정책 | ✅ |
| `app-secrets.sample.json` | Secrets Manager 등록 샘플 (실제 IP 반영) | ✅ 참조용 |

---

## 공통 변수

```bash
export REGION=ap-northeast-2
export ACCOUNT_ID=086015456585

export APP_NAME=lumina-invest
export CLUSTER_NAME=lumina-prod
export ECR_REPO=lumina-invest
export ECS_EXEC_ROLE=ecsTaskExecutionRole
export ECS_TASK_ROLE=luminaInvestTaskRole
export SCHED_ROLE=luminaSchedulerRole

export VPC_ID=vpc-01879d70df92987b9
export PUB_SUBNET_1=subnet-0b7dcd7ba9c6836c0           # ap-northeast-2c (public)
export PRI_SUBNET_1=subnet-0d28ba1be9a1ff04c            # ap-northeast-2a (ECS/ALB)
export PRI_SUBNET_2=subnet-0e0cd6292f59623af             # ap-northeast-2b (ECS/ALB)

export ALB_SG=sg-0cab122da7c919d78                      # lumina-alb-sg
export ECS_SG=sg-0baee9bc5a2ea90b0                      # lumina-ecs-sg
export DOCDB_SG=sg-02cd84bcccf1eb18f                    # lumina-docdb-sg
export EC2_SG=sg-0dc3ec23151b1ce26                      # launch-wizard-1

export INSTANCE_ID=i-0af91b80c1ada35f3
export INSTANCE_PRIVATE_IP=172.30.2.131

export FRONT_BUCKET=lumina-invest-front-${ACCOUNT_ID}
export OAC_ID=E2HVAGOCBF9LUY
export CF_ID=E2Z5V1W67DDW54
export CF_DOMAIN=d3ls3wdarllhnf.cloudfront.net

export DOCDB_ENDPOINT=lumina-docdb.cluster-cg0ugoglztrn.ap-northeast-2.docdb.amazonaws.com
export DOCDB_SUBNET_GROUP=lumina-docdb-subnet-group
export DOCDB_CLUSTER=lumina-docdb

export ALB_ARN=arn:aws:elasticloadbalancing:${REGION}:${ACCOUNT_ID}:loadbalancer/app/lumina-alb/2766a63190a2b3a4
export TG_ARN=arn:aws:elasticloadbalancing:${REGION}:${ACCOUNT_ID}:targetgroup/lumina-api-tg/a8a20e4493ab33d9
export SECRET_ARN=arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:lumina-invest/prod/app-c4WE2y
```

---

## 트러블슈팅 이력 (배포 과정에서 해결한 문제)

| # | 증상 | 원인 | 해결 |
|---|------|------|------|
| 1 | CloudFront AccessDenied | S3 버킷 정책 없음 (OAC 미적용) | `s3:GetObject` 버킷 정책 추가 (CloudFront distribution ARN 조건) |
| 2 | ECS 503 / SM 연결 불가 | Private 서브넷에서 Secrets Manager 라우팅 없음 | `assignPublicIp=ENABLED` + 라우트 테이블 IGW 경유 |
| 3 | AccessDeniedException (SM) | `ecsTaskExecutionRole`에 SM 권한 없음 | 인라인 정책 `SecretsManagerReadAccess` 추가 |
| 4 | `json key MONGO_URI` 없음 | 시크릿 생성 시 MONGO_URI 키 누락 | Secrets Manager에 키 추가 |
| 5 | Target.NotInUse | ECS 서브넷(2c/2d) ≠ ALB 활성 AZ(2a/2b) | ECS 서비스 서브넷을 `subnet-0d28ba1be9a1ff04c` / `subnet-0e0cd6292f59623af`으로 교정 |
| 6 | DocDB SSL CERTIFICATE_VERIFY_FAILED | 컨테이너에 RDS CA 인증서 없음 | Dockerfile에 `rds-global-bundle` 추가 후 재빌드/재푸시 |
| 7 | DocDB Authentication failed | MONGO_URI 비밀번호 미설정 | ⏳ 실제 마스터 비밀번호로 교체 필요 |

---

## 남은 작업 순서

### 1. DocDB 비밀번호 업데이트 (필수)

```bash
python3 - <<'EOF'
import subprocess, json, re

result = subprocess.run(
    ["aws", "secretsmanager", "get-secret-value",
     "--secret-id", "lumina-invest/prod/app",
     "--query", "SecretString", "--output", "text"],
    capture_output=True, text=True
)
secret = json.loads(result.stdout.strip())

# 실제 비밀번호로 교체
ACTUAL_PASSWORD = "여기에실제비밀번호"
secret["MONGO_URI"] = re.sub(
    r'(mongodb://docdbuser:)[^@]+(@)',
    rf'\g<1>{ACTUAL_PASSWORD}\2',
    secret["MONGO_URI"]
)

subprocess.run(
    ["aws", "secretsmanager", "update-secret",
     "--secret-id", "lumina-invest/prod/app",
     "--secret-string", json.dumps(secret)],
    capture_output=True, text=True
)
print("완료")
EOF

# 업데이트 후 재배포
aws ecs update-service --cluster lumina-prod \
  --service lumina-invest-api-svc --force-new-deployment \
  --region $REGION
aws ecs update-service --cluster lumina-prod \
  --service lumina-invest-worker-svc --force-new-deployment \
  --region $REGION
```

### 2. ECR 이미지 업데이트 (코드 변경 시)

```bash
aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com

docker build -t ${ECR_REPO}:latest /home/ubuntu/lumina-invest
docker tag ${ECR_REPO}:latest ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}:latest
docker push ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}:latest

# 재배포
aws ecs update-service --cluster $CLUSTER_NAME \
  --service ${APP_NAME}-api-svc --force-new-deployment --region $REGION
aws ecs update-service --cluster $CLUSTER_NAME \
  --service ${APP_NAME}-worker-svc --force-new-deployment --region $REGION
```

### 3. ECS Task Definition 재등록 (태스크 설정 변경 시)

```bash
aws ecs register-task-definition \
  --cli-input-json file://aws-work/api-taskdef.json --region $REGION

aws ecs register-task-definition \
  --cli-input-json file://aws-work/worker-taskdef.json --region $REGION
```

### 4. Scheduler Role 정책 적용

```bash
aws iam put-role-policy \
  --role-name $SCHED_ROLE \
  --policy-name scheduler-ecs-run-task \
  --policy-document file://aws-work/scheduler-ecs-policy.json
```

### 5. EventBridge Scheduler 생성

```bash
# 시간당 시장 데이터 동기화
aws scheduler create-schedule \
  --name lumina-hourly-sync \
  --schedule-expression "rate(1 hour)" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target "{
    \"Arn\":\"arn:aws:ecs:${REGION}:${ACCOUNT_ID}:cluster/${CLUSTER_NAME}\",
    \"RoleArn\":\"arn:aws:iam::${ACCOUNT_ID}:role/${SCHED_ROLE}\",
    \"EcsParameters\":{
      \"TaskDefinitionArn\":\"arn:aws:ecs:${REGION}:${ACCOUNT_ID}:task-definition/${APP_NAME}-worker\",
      \"LaunchType\":\"FARGATE\",
      \"NetworkConfiguration\":{
        \"awsvpcConfiguration\":{
          \"Subnets\":[\"${PRI_SUBNET_1}\",\"${PRI_SUBNET_2}\"],
          \"SecurityGroups\":[\"${ECS_SG}\"],
          \"AssignPublicIp\":\"ENABLED\"
        }
      }
    }
  }" \
  --region $REGION

# 일별 캔들 동기화
aws scheduler create-schedule \
  --name lumina-daily-candles \
  --schedule-expression "rate(1 day)" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target "{
    \"Arn\":\"arn:aws:ecs:${REGION}:${ACCOUNT_ID}:cluster/${CLUSTER_NAME}\",
    \"RoleArn\":\"arn:aws:iam::${ACCOUNT_ID}:role/${SCHED_ROLE}\",
    \"EcsParameters\":{
      \"TaskDefinitionArn\":\"arn:aws:ecs:${REGION}:${ACCOUNT_ID}:task-definition/${APP_NAME}-worker\",
      \"LaunchType\":\"FARGATE\",
      \"NetworkConfiguration\":{
        \"awsvpcConfiguration\":{
          \"Subnets\":[\"${PRI_SUBNET_1}\",\"${PRI_SUBNET_2}\"],
          \"SecurityGroups\":[\"${ECS_SG}\"],
          \"AssignPublicIp\":\"ENABLED\"
        }
      }
    }
  }" \
  --region $REGION
```

### 6. 프론트 정적 파일 S3 업로드

```bash
aws s3 sync /home/ubuntu/lumina-invest/public/ s3://$FRONT_BUCKET/ --delete
```

---

## Security Group 현황

| SG | ID | 허용 규칙 |
|----|----|---------|
| lumina-alb-sg | `sg-0cab122da7c919d78` | 0.0.0.0/0 → TCP 80 |
| lumina-ecs-sg | `sg-0baee9bc5a2ea90b0` | lumina-alb-sg → TCP 8000 |
| lumina-docdb-sg | `sg-02cd84bcccf1eb18f` | lumina-ecs-sg → TCP 27017 |
| launch-wizard-1 | `sg-0dc3ec23151b1ce26` | lumina-ecs-sg → TCP 6379/11434/6333/7687 |

## IAM 역할 현황

| 역할 | ARN | 연결 정책 |
|------|-----|---------|
| ecsTaskExecutionRole | `arn:aws:iam::086015456585:role/ecsTaskExecutionRole` | AmazonECSTaskExecutionRolePolicy + SecretsManagerReadAccess (inline) |
| luminaInvestTaskRole | `arn:aws:iam::086015456585:role/luminaInvestTaskRole` | — |
| luminaSchedulerRole | `arn:aws:iam::086015456585:role/luminaSchedulerRole` | scheduler-ecs-run-task (inline, 적용 대기) |

## 운영 체크 포인트

- `app/main.py` 인프로세스 스케줄러(`start_sync_scheduler()`)는 EventBridge Scheduler와 중복 → 운영 전 비활성화 권장
- EC2 단일 장애점 모니터링: `i-0af91b80c1ada35f3` CPU/메모리/디스크 CloudWatch 알람 설정 권장
- CloudFront → ALB 구간은 HTTP, 사용자 → CloudFront 구간은 HTTPS (현재 구조)
- 로그 확인: `aws logs tail /ecs/lumina-invest-api --follow`
