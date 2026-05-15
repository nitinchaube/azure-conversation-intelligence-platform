"""Environment configuration for the live voice module.

Designed to coexist with the existing batch pipeline in
``custom_extensions/04_compliance_check.py``. That pipeline authenticates
to Azure OpenAI via ``DefaultAzureCredential``; here we support both
patterns:

* If ``AZURE_OPENAI_API_KEY`` is set we use it directly.
* Otherwise we fall back to AAD via ``DefaultAzureCredential``.

The deployment name is read from either ``AZURE_OPENAI_DEPLOYMENT`` or the
legacy ``AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME`` env var that the batch
pipeline already uses.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    speech_key: str
    speech_region: str
    speech_language: str

    aoai_endpoint: str
    aoai_deployment: str
    aoai_api_version: str
    aoai_api_key: Optional[str]

    @property
    def use_api_key(self) -> bool:
        return bool(self.aoai_api_key)

    @classmethod
    def from_env(cls) -> "Settings":
        speech_key = os.getenv("AZURE_SPEECH_KEY")
        speech_region = os.getenv("AZURE_SPEECH_REGION")
        aoai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        aoai_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT") or os.getenv(
            "AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME"
        )

        required = {
            "AZURE_SPEECH_KEY": speech_key,
            "AZURE_SPEECH_REGION": speech_region,
            "AZURE_OPENAI_ENDPOINT": aoai_endpoint,
            "AZURE_OPENAI_DEPLOYMENT (or AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME)": aoai_deployment,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
            )

        return cls(
            speech_key=speech_key,  # type: ignore[arg-type]
            speech_region=speech_region,  # type: ignore[arg-type]
            speech_language=os.getenv("AZURE_SPEECH_LANGUAGE", "en-US"),
            aoai_endpoint=aoai_endpoint,  # type: ignore[arg-type]
            aoai_deployment=aoai_deployment,  # type: ignore[arg-type]
            aoai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            aoai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        )


SETTINGS = Settings.from_env()
