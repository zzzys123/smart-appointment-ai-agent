"""Python Agent 访问预约后端时使用的配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class AppointmentBackendConfig:
    """预约后端选择与 Java HTTP 客户端配置。"""

    backend: str = "local"
    java_base_url: str = "http://127.0.0.1:8080"
    timeout_seconds: float = 3.0

    @classmethod
    def from_env(cls) -> "AppointmentBackendConfig":
        backend = os.getenv("APPOINTMENT_BACKEND", "local").strip().lower()
        base_url = os.getenv(
            "JAVA_APPOINTMENT_BASE_URL", "http://127.0.0.1:8080"
        ).strip()
        timeout_seconds = float(
            os.getenv("JAVA_APPOINTMENT_TIMEOUT_SECONDS", "3")
        )

        if backend not in {"local", "java"}:
            raise ValueError(
                "APPOINTMENT_BACKEND must be either 'local' or 'java'"
            )
        if not base_url:
            raise ValueError("JAVA_APPOINTMENT_BASE_URL must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("JAVA_APPOINTMENT_TIMEOUT_SECONDS must be positive")

        return cls(
            backend=backend,
            java_base_url=base_url.rstrip("/"),
            timeout_seconds=timeout_seconds,
        )
