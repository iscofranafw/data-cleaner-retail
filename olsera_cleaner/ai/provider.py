"""Multi-provider AI abstraction supporting local Ollama, authenticated remote AI, and privacy guardrails."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
import json
import logging
import re
from typing import Any, Dict, Optional
import requests

logger = logging.getLogger(__name__)

_ALLOWED_MODEL = re.compile(r"^[A-Za-z0-9_.:/\-]{1,150}$")


class AIProviderType(str, Enum):
    OLLAMA = "OLLAMA"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"
    GEMINI = "GEMINI"
    DISABLED = "DISABLED"


@dataclass
class AIResponse:
    content: str
    parsed_json: Optional[Dict[str, Any]]
    success: bool
    error: Optional[str] = None
    is_fallback: bool = False


class BaseAIProvider(ABC):
    """Abstract interface for AI inference providers."""

    @abstractmethod
    def generate(self, prompt: str, json_format: bool = True) -> AIResponse:
        """Execute text generation or JSON structured output."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check provider reachability."""
        pass

    @property
    @abstractmethod
    def is_external(self) -> bool:
        """Whether this provider sends data to an external 3rd-party cloud service."""
        pass


class OllamaProvider(BaseAIProvider):
    """Local Ollama inference provider."""

    def __init__(self, endpoint: str = "http://127.0.0.1:11434", model: str = "llama3:8b", timeout: int = 30):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def is_external(self) -> bool:
        return False

    def is_available(self) -> bool:
        try:
            r = requests.get(f"{self.endpoint}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def generate(self, prompt: str, json_format: bool = True) -> AIResponse:
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0},
        }
        if json_format:
            payload["format"] = "json"

        try:
            r = requests.post(f"{self.endpoint}/api/generate", json=payload, timeout=self.timeout)
            r.raise_for_status()
            text = r.json().get("response", "")
            parsed = json.loads(text) if json_format else None
            return AIResponse(content=text, parsed_json=parsed, success=True)
        except Exception as exc:
            return AIResponse(content="", parsed_json=None, success=False, error=str(exc), is_fallback=True)


class OpenAICompatibleProvider(BaseAIProvider):
    """Remote OpenAI/vLLM/OpenRouter compatible inference provider."""

    def __init__(self, endpoint: str, api_key: str, model: str = "gpt-4o-mini", timeout: int = 30):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def is_external(self) -> bool:
        return True

    def is_available(self) -> bool:
        return bool(self.endpoint and self.api_key)

    def generate(self, prompt: str, json_format: bool = True) -> AIResponse:
        url = f"{self.endpoint}/chat/completions" if not self.endpoint.endswith("/chat/completions") else self.endpoint
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        }
        if json_format:
            payload["response_format"] = {"type": "json_object"}

        try:
            r = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"]
            parsed = json.loads(text) if json_format else None
            return AIResponse(content=text, parsed_json=parsed, success=True)
        except Exception as exc:
            return AIResponse(content="", parsed_json=None, success=False, error=str(exc), is_fallback=True)


class GeminiProvider(BaseAIProvider):
    """Remote Google Gemini REST API inference provider."""

    def __init__(self, api_key: str, model: str = "gemini-1.5-flash", timeout: int = 30):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def is_external(self) -> bool:
        return True

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, json_format: bool = True) -> AIResponse:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        generation_config: Dict[str, Any] = {"temperature": 0.0}
        if json_format:
            generation_config["responseMimeType"] = "application/json"

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }

        try:
            r = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text) if json_format else None
            return AIResponse(content=text, parsed_json=parsed, success=True)
        except Exception as exc:
            return AIResponse(content="", parsed_json=None, success=False, error=str(exc), is_fallback=True)


class DisabledAIProvider(BaseAIProvider):
    """Deterministic fallback provider when AI is disabled or offline."""

    @property
    def is_external(self) -> bool:
        return False

    def is_available(self) -> bool:
        return True

    def generate(self, prompt: str, json_format: bool = True) -> AIResponse:
        return AIResponse(
            content="{}",
            parsed_json={},
            success=False,
            error="AI provider disabled or offline",
            is_fallback=True,
        )


def get_ai_provider(
    provider_type: str = "OLLAMA",
    endpoint: str = "http://127.0.0.1:11434",
    model: str = "llama3:8b",
    api_key: str = "",
    allow_external_sharing: bool = False,
    timeout: int = 30,
) -> BaseAIProvider:
    """Factory creating configured AI provider with privacy guardrail enforcement."""
    p_type = str(provider_type or "OLLAMA").upper().strip()

    if p_type == AIProviderType.DISABLED.value or p_type in ("OFFLINE", "NONE", "FALSE", ""):
        return DisabledAIProvider()

    if p_type == AIProviderType.OLLAMA.value:
        return OllamaProvider(endpoint=endpoint, model=model, timeout=timeout)

    # Privacy Guardrail check for external providers
    if not allow_external_sharing:
        logger.warning(
            "PENCEGAHAN PRIVASI: Pengiriman data ke provider AI eksternal (%s) diblokir karena "
            "ALLOW_EXTERNAL_AI_DATA_SHARING=False. Menggunakan mode deterministic fallback.",
            p_type,
        )
        return DisabledAIProvider()

    if p_type == AIProviderType.OPENAI_COMPATIBLE.value:
        if not api_key:
            logger.warning("AI_API_KEY kosong untuk OpenAICompatibleProvider.")
            return DisabledAIProvider()
        return OpenAICompatibleProvider(endpoint=endpoint, api_key=api_key, model=model, timeout=timeout)

    if p_type == AIProviderType.GEMINI.value:
        if not api_key:
            logger.warning("AI_API_KEY kosong untuk GeminiProvider.")
            return DisabledAIProvider()
        return GeminiProvider(api_key=api_key, model=model, timeout=timeout)

    return DisabledAIProvider()
