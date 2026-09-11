"""LLM 서빙 백엔드 추상화.

settings.LLM_PROVIDER 에 따라 채팅(생성)에 사용할 백엔드를 고른다:
  - ollama    : 기존 방식 (로컬 또는 EC2 위의 Ollama, 기본값)
  - bedrock   : Amazon Bedrock (converse API)
  - sagemaker : SageMaker JumpStart 엔드포인트 (invoke_endpoint)
  - vllm      : EC2/ECS 위의 vLLM (OpenAI 호환 /v1/chat/completions)

임베딩(embed)은 이 앱의 RAG/문서 파이프라인이 전부 nomic-embed-text 차원(768)에
맞춰져 있으므로 어떤 provider를 고르든 항상 로컬 Ollama로 위임한다 — Bedrock/
SageMaker/vLLM 쪽 채팅 모델은 이 앱에서 임베딩 용도로 쓰지 않는다.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol

import httpx

from app.config import settings
from app.lib.ollama import OllamaClient


class LLMClient(Protocol):
    async def chat(self, model: str, messages: list[dict], options: dict | None = None) -> str: ...
    async def embed(self, model: str, input_text: str) -> list[float]: ...


class _EmbedViaOllamaMixin:
    """embed()는 항상 로컬 Ollama(settings.EMBED_MODEL)로 위임."""

    def __init__(self) -> None:
        self._embed_client = OllamaClient(settings.OLLAMA_BASE_URL, settings.OLLAMA_TIMEOUT)

    async def embed(self, model: str, input_text: str) -> list[float]:
        return await self._embed_client.embed(settings.EMBED_MODEL, input_text)


class BedrockClient(_EmbedViaOllamaMixin):
    """Amazon Bedrock converse API. Claude/Llama/Mistral 등 provider 무관하게 동일 인터페이스."""

    def __init__(self, model_id: str, region: str):
        super().__init__()
        if not model_id:
            raise RuntimeError("LLM_PROVIDER=bedrock 인데 BEDROCK_MODEL_ID가 설정되지 않았습니다.")
        import boto3

        self._model_id = model_id
        self._client = boto3.client("bedrock-runtime", region_name=region)

    async def chat(self, model: str, messages: list[dict], options: dict | None = None) -> str:
        return await asyncio.to_thread(self._chat_sync, messages, options or {})

    def _chat_sync(self, messages: list[dict], options: dict) -> str:
        system_blocks = [{"text": m["content"]} for m in messages if m.get("role") == "system"]
        turns = [
            {"role": m["role"], "content": [{"text": m["content"]}]}
            for m in messages
            if m.get("role") in ("user", "assistant")
        ]
        kwargs: dict[str, Any] = {
            "modelId": self._model_id,
            "messages": turns,
            "inferenceConfig": {
                "maxTokens": options.get("num_predict", 1024),
                "temperature": options.get("temperature", 0.7),
            },
        }
        if system_blocks:
            kwargs["system"] = system_blocks

        resp = self._client.converse(**kwargs)
        return resp["output"]["message"]["content"][0]["text"]


class SageMakerClient(_EmbedViaOllamaMixin):
    """SageMaker JumpStart 채팅 엔드포인트 (LMI/TGI 컨테이너, OpenAI 호환 messages 스키마)."""

    def __init__(self, endpoint_name: str, region: str):
        super().__init__()
        if not endpoint_name:
            raise RuntimeError("LLM_PROVIDER=sagemaker 인데 SAGEMAKER_ENDPOINT_NAME이 설정되지 않았습니다.")
        import boto3

        self._endpoint = endpoint_name
        self._client = boto3.client("sagemaker-runtime", region_name=region)

    async def chat(self, model: str, messages: list[dict], options: dict | None = None) -> str:
        return await asyncio.to_thread(self._chat_sync, messages, options or {})

    def _chat_sync(self, messages: list[dict], options: dict) -> str:
        body = {
            "messages": messages,
            "max_tokens": options.get("num_predict", 1024),
            "temperature": options.get("temperature", 0.7),
        }
        resp = self._client.invoke_endpoint(
            EndpointName=self._endpoint,
            ContentType="application/json",
            Body=json.dumps(body),
        )
        payload = json.loads(resp["Body"].read())
        return self._extract_text(payload)

    @staticmethod
    def _extract_text(payload: Any) -> str:
        if isinstance(payload, list) and payload:
            payload = payload[0]
        if isinstance(payload, dict):
            if "choices" in payload and payload["choices"]:
                choice = payload["choices"][0]
                return choice.get("message", {}).get("content") or choice.get("text", "")
            if "generated_text" in payload:
                return payload["generated_text"]
        return json.dumps(payload, ensure_ascii=False)


class VLLMClient(_EmbedViaOllamaMixin):
    """EC2/ECS 위의 vLLM — OpenAI 호환 /v1/chat/completions."""

    def __init__(self, base_url: str, model: str, timeout: float):
        super().__init__()
        if not base_url:
            raise RuntimeError("LLM_PROVIDER=vllm 인데 VLLM_BASE_URL이 설정되지 않았습니다.")
        self._base = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    async def chat(self, model: str, messages: list[dict], options: dict | None = None) -> str:
        opts = options or {}
        payload = {
            "model": self._model or model,
            "messages": messages,
            "max_tokens": opts.get("num_predict", 1024),
            "temperature": opts.get("temperature", 0.7),
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]


_client_cache: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """settings.LLM_PROVIDER에 따라 채팅 백엔드 클라이언트를 반환 (프로세스당 1회 생성 후 캐시)."""
    global _client_cache
    if _client_cache is not None:
        return _client_cache

    provider = settings.LLM_PROVIDER.lower()
    if provider == "bedrock":
        _client_cache = BedrockClient(settings.BEDROCK_MODEL_ID, settings.AWS_REGION)
    elif provider == "sagemaker":
        _client_cache = SageMakerClient(settings.SAGEMAKER_ENDPOINT_NAME, settings.AWS_REGION)
    elif provider == "vllm":
        _client_cache = VLLMClient(settings.VLLM_BASE_URL, settings.VLLM_MODEL, settings.OLLAMA_TIMEOUT)
    elif provider == "ollama":
        _client_cache = OllamaClient(settings.OLLAMA_BASE_URL, settings.OLLAMA_TIMEOUT)
    else:
        raise RuntimeError(f"알 수 없는 LLM_PROVIDER: {settings.LLM_PROVIDER!r} (ollama/bedrock/sagemaker/vllm 중 하나)")
    return _client_cache
