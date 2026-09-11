"""SageMaker JumpStart LLM 채팅 엔드포인트 배포/삭제 스크립트.

기본값: Llama 3.2 3B Instruct on ml.g5.xlarge (시간당 약 $1~1.5, us-east-1/ap-northeast-2
등 GPU 인스턴스 가용 리전 기준). 이 인스턴스는 삭제 전까지 계속 과금되므로,
사용이 끝나면 반드시 --delete로 정리할 것.

app 패키지에 의존하지 않는 독립 실행 스크립트이며, 실행 시 lumina-invest venv가
아니라 아래 requirements-deploy.txt를 설치한 별도 환경에서 돌리는 걸 권장한다
(SageMaker Python SDK는 운영 서버(FastAPI/Celery)에는 불필요).

사용법:
    pip install -r sagemaker/requirements-deploy.txt

    # 배포 (실제 과금 시작)
    python sagemaker/deploy_llm_endpoint.py deploy \\
        --endpoint-name lumina-llama-3-2-3b-instruct \\
        --execution-role arn:aws:iam::<account-id>:role/lumina-sagemaker-execution-role \\
        --region ap-northeast-2

    # 상태 확인 (무료)
    python sagemaker/deploy_llm_endpoint.py status --endpoint-name lumina-llama-3-2-3b-instruct

    # 삭제 (과금 중단)
    python sagemaker/deploy_llm_endpoint.py delete --endpoint-name lumina-llama-3-2-3b-instruct

배포가 끝나면 앱의 .env에 다음을 설정:
    LLM_PROVIDER=sagemaker
    SAGEMAKER_ENDPOINT_NAME=lumina-llama-3-2-3b-instruct
"""
from __future__ import annotations

import argparse
import sys

DEFAULT_MODEL_ID = "meta-textgeneration-llama-3-2-3b-instruct"
DEFAULT_INSTANCE_TYPE = "ml.g5.xlarge"


def cmd_deploy(args: argparse.Namespace) -> None:
    from sagemaker.jumpstart.model import JumpStartModel

    print(f"[deploy] model_id={args.model_id} instance_type={args.instance_type} "
          f"endpoint_name={args.endpoint_name} region={args.region}")
    print("[deploy] 이 인스턴스는 삭제 전까지 시간당 과금됩니다. 계속하려면 EULA에 동의해야 합니다.")

    model = JumpStartModel(
        model_id=args.model_id,
        role=args.execution_role,
        instance_type=args.instance_type,
        region=args.region,
    )
    predictor = model.deploy(
        endpoint_name=args.endpoint_name,
        accept_eula=True,
        initial_instance_count=1,
    )
    print(f"[deploy] 완료. endpoint_name={predictor.endpoint_name}")
    print("[deploy] .env에 다음을 설정하세요:")
    print("  LLM_PROVIDER=sagemaker")
    print(f"  SAGEMAKER_ENDPOINT_NAME={predictor.endpoint_name}")


def cmd_status(args: argparse.Namespace) -> None:
    import boto3

    client = boto3.client("sagemaker", region_name=args.region)
    try:
        resp = client.describe_endpoint(EndpointName=args.endpoint_name)
    except client.exceptions.ClientError as e:
        print(f"[status] 조회 실패: {e}")
        sys.exit(1)

    print(f"[status] EndpointName={resp['EndpointName']}")
    print(f"[status] EndpointStatus={resp['EndpointStatus']}")
    print(f"[status] InstanceType은 EndpointConfig에서 확인: {resp['EndpointConfigName']}")
    if resp["EndpointStatus"] == "InService":
        print("[status] 현재 과금 중입니다. 사용이 끝나면 delete 명령으로 정리하세요.")


def cmd_delete(args: argparse.Namespace) -> None:
    import boto3

    client = boto3.client("sagemaker", region_name=args.region)

    try:
        endpoint = client.describe_endpoint(EndpointName=args.endpoint_name)
        config_name = endpoint["EndpointConfigName"]
        config = client.describe_endpoint_config(EndpointConfigName=config_name)
        model_names = [v["ModelName"] for v in config["ProductionVariants"]]
    except client.exceptions.ClientError as e:
        print(f"[delete] endpoint 조회 실패 (이미 삭제됐을 수 있음): {e}")
        model_names, config_name = [], None

    client.delete_endpoint(EndpointName=args.endpoint_name)
    print(f"[delete] endpoint 삭제 요청 완료: {args.endpoint_name}")

    if config_name:
        client.delete_endpoint_config(EndpointConfigName=config_name)
        print(f"[delete] endpoint config 삭제 완료: {config_name}")

    for m in model_names:
        client.delete_model(ModelName=m)
        print(f"[delete] model 삭제 완료: {m}")

    print("[delete] 과금이 중단되었습니다.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    common = dict(
        endpoint_name=dict(default="lumina-llama-3-2-3b-instruct", help="SageMaker 엔드포인트 이름"),
        region=dict(default="ap-northeast-2", help="AWS 리전"),
    )

    p_deploy = sub.add_parser("deploy", help="엔드포인트 배포 (과금 시작)")
    p_deploy.add_argument("--endpoint-name", **common["endpoint_name"])
    p_deploy.add_argument("--region", **common["region"])
    p_deploy.add_argument("--model-id", default=DEFAULT_MODEL_ID, help="JumpStart 모델 ID")
    p_deploy.add_argument("--instance-type", default=DEFAULT_INSTANCE_TYPE)
    p_deploy.add_argument("--execution-role", required=True,
                           help="SageMaker 실행 역할 ARN (예: lumina-sagemaker-execution-role)")
    p_deploy.set_defaults(func=cmd_deploy)

    p_status = sub.add_parser("status", help="엔드포인트 상태 조회 (무료)")
    p_status.add_argument("--endpoint-name", **common["endpoint_name"])
    p_status.add_argument("--region", **common["region"])
    p_status.set_defaults(func=cmd_status)

    p_delete = sub.add_parser("delete", help="엔드포인트/모델/설정 삭제 (과금 중단)")
    p_delete.add_argument("--endpoint-name", **common["endpoint_name"])
    p_delete.add_argument("--region", **common["region"])
    p_delete.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
