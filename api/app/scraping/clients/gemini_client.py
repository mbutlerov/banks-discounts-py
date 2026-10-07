from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Any

import logging

import requests
from requests import Response, Session
from requests.exceptions import RequestException, Timeout

logger = logging.getLogger(__name__)


DEFAULT_TIMEOUT = 60
DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiClientError(Exception):
    """Error base del cliente Gemini."""


class GeminiClientConfigError(GeminiClientError):
    """Error de configuración del cliente Gemini."""


class GeminiClientRequestError(GeminiClientError):
    """Error al invocar la API de Gemini."""


class GeminiClientResponseError(GeminiClientError):
    """Error al interpretar la respuesta de Gemini."""


@dataclass(slots=True)
class GeminiGenerationResult:
    raw_response: dict[str, Any]
    text: str
    parsed_json: dict[str, Any] | list[Any] | None = None


class GeminiClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
        session: Session | None = None,
    ) -> None:
        if not api_key:
            raise GeminiClientConfigError(
                "Falta configurar GEMINI_API_KEY para usar GeminiClient."
            )

        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._session = session or requests.Session()
        self._base_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent"
        )

    def generate_json(
        self,
        *,
        prompt: str,
        response_json_schema: dict[str, Any] | None = None,
        temperature: float = 0.1,
        top_p: float = 0.95,
        top_k: int = 40,
        max_output_tokens: int = 512,
    ) -> GeminiGenerationResult:
        generation_config: dict[str, Any] = {
            "responseMimeType": "application/json",
            "temperature": temperature,
            "topP": top_p,
            "topK": top_k,
            "maxOutputTokens": max_output_tokens,
            "thinkingConfig": {
                "thinkingBudget": 0
            },
        }

        if response_json_schema is not None:
            generation_config["responseJsonSchema"] = response_json_schema

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": prompt,
                        }
                    ]
                }
            ],
            "generationConfig": generation_config,
        }

        raw_response = self._post_with_retry(payload)

        text = self._extract_text(raw_response)
        parsed_json = self._safe_json_loads(text)

        return GeminiGenerationResult(
            raw_response=raw_response,
            text=text,
            parsed_json=parsed_json,
        )

    def generate_json_with_pdf(
        self,
        *,
        prompt: str,
        pdf_bytes: bytes,
        mime_type: str = "application/pdf",
        response_json_schema: dict[str, Any] | None = None,
        temperature: float = 0.1,
        max_output_tokens: int = 512,
    ) -> GeminiGenerationResult:
        generation_config: dict[str, Any] = {
            "responseMimeType": "application/json",
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "thinkingConfig": {"thinkingBudget": 0},
        }
        if response_json_schema is not None:
            generation_config["responseJsonSchema"] = response_json_schema

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(pdf_bytes).decode(),
                            }
                        },
                        {"text": prompt},
                    ]
                }
            ],
            "generationConfig": generation_config,
        }

        raw_response = self._post_with_retry(payload)
        text = self._extract_text(raw_response)
        parsed_json = self._safe_json_loads(text)
        return GeminiGenerationResult(
            raw_response=raw_response,
            text=text,
            parsed_json=parsed_json,
        )

    def _post_with_retry(
        self,
        payload: dict[str, Any],
        *,
        max_attempts: int = 3,
        retry_sleep_seconds: int = 5,
    ) -> dict[str, Any]:
        last_exception: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            response: Response | None = None

            try:
                response = self._session.post(
                    self._base_url,
                    json=payload,
                    timeout=self._timeout,
                    headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key},
                )
                response.raise_for_status()
                return response.json()

            except Timeout as exc:
                last_exception = exc
                if attempt < max_attempts:
                    time.sleep(retry_sleep_seconds * attempt)
                    continue

                raise GeminiClientRequestError(
                    "La request a Gemini excedió el timeout configurado."
                ) from exc

            except RequestException as exc:
                last_exception = exc

                if response is not None:
                    status_code = response.status_code

                    logger.warning(
                        "[GeminiClient] RequestException intento %d/%d -> status=%d",
                        attempt, max_attempts, status_code,
                    )

                    if status_code == 429 and attempt < max_attempts:
                        time.sleep(retry_sleep_seconds * attempt)
                        continue

                    raise GeminiClientRequestError(
                        f"Falló la request a Gemini. "
                        f"status_code={status_code}"
                    ) from exc

                raise GeminiClientRequestError(
                    "Falló la request a Gemini sin respuesta HTTP."
                ) from exc

        raise GeminiClientRequestError(
            "Falló la request a Gemini luego de varios intentos."
        ) from last_exception

    @staticmethod
    def _extract_text(response_json: dict[str, Any]) -> str:
        try:
            candidates = response_json["candidates"]
            first_candidate = candidates[0]
            content = first_candidate["content"]
            parts = content["parts"]

            texts: list[str] = []
            for part in parts:
                part_text = part.get("text")
                if part_text:
                    texts.append(part_text)

            if not texts:
                raise GeminiClientResponseError(
                    "Gemini respondió sin texto utilizable."
                )

            return "\n".join(texts).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise GeminiClientResponseError(
                "No se pudo extraer el texto de la respuesta de Gemini."
            ) from exc

    @staticmethod
    def _safe_json_loads(text: str) -> dict[str, Any] | list[Any] | None:
        if not text.strip():
            return None

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        fenced_match = re.search(
            r"```json\s*(.*?)\s*```",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if fenced_match:
            fenced_body = fenced_match.group(1).strip()
            try:
                return json.loads(fenced_body)
            except json.JSONDecodeError:
                return None

        return None
