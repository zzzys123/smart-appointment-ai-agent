"""通过 HTTP 调用 Spring Boot 预约领域服务。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from config.time_config import time_config

from .base import (
    AppointmentBackendUnavailableError,
    AppointmentConflictError,
    AppointmentRequestError,
    AppointmentResult,
    CreateAppointmentCommand,
    TechnicianData,
)


logger = logging.getLogger(__name__)


class JavaAppointmentGateway:
    def __init__(self, base_url: str, timeout_seconds: float = 3.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def list_technicians(self) -> List[TechnicianData]:
        payload, _ = await self._request(
            "GET", "/internal/v1/technicians"
        )
        if not isinstance(payload, list):
            raise AppointmentBackendUnavailableError(
                "Java appointment service returned an invalid technician list"
            )
        return payload

    async def find_available_technicians(
        self,
        start_time: datetime,
        duration_minutes: int,
        gender: Optional[str] = None,
        strength: Optional[str] = None,
    ) -> List[TechnicianData]:
        params: Dict[str, Any] = {
            "startTime": self._format_local_datetime(start_time),
            "durationMinutes": duration_minutes,
        }
        if gender:
            params["gender"] = gender
        if strength:
            params["strength"] = strength

        payload, _ = await self._request(
            "GET",
            "/internal/v1/technicians/available",
            params=params,
        )
        if not isinstance(payload, list):
            raise AppointmentBackendUnavailableError(
                "Java appointment service returned an invalid availability list"
            )
        return payload

    async def create_appointment(
        self,
        command: CreateAppointmentCommand,
        idempotency_key: str,
    ) -> AppointmentResult:
        headers = {"Idempotency-Key": idempotency_key}
        if command.trace_id:
            headers["X-Trace-Id"] = command.trace_id

        payload, response_status = await self._request(
            "POST",
            "/internal/v1/appointments",
            headers=headers,
            json={
                "userId": command.user_id,
                "sessionId": command.session_id,
                "technicianId": command.technician_id,
                "serviceName": command.service_name,
                "startTime": self._format_local_datetime(command.start_time),
                "durationMinutes": command.duration_minutes,
            },
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not data.get("appointmentNo"):
            raise AppointmentBackendUnavailableError(
                "Java appointment service returned an invalid create response"
            )
        result = AppointmentResult(
            appointment_no=str(data["appointmentNo"]),
            status=str(data.get("status", "CONFIRMED")),
            created=response_status == 201,
        )
        logger.info(
            "java_appointment_result trace_id=%s session_id=%s appointment_no=%s outcome=%s",
            command.trace_id or "-",
            command.session_id,
            result.appointment_no,
            "created" if result.created else "replayed",
        )
        return result

    async def _request(self, method: str, path: str, **kwargs):
        url = f"{self._base_url}{path}"
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.request(method, url, **kwargs) as response:
                    payload = await self._read_payload(response)
                    if response.status == 409:
                        raise AppointmentConflictError(
                            self._message(payload, "Appointment slot conflict")
                        )
                    if 400 <= response.status < 500:
                        raise AppointmentRequestError(
                            self._message(payload, "Appointment request was rejected"),
                            self._code(payload, "APPOINTMENT_REQUEST_REJECTED"),
                        )
                    if response.status >= 500:
                        raise AppointmentBackendUnavailableError(
                            f"Java appointment service returned HTTP {response.status}"
                        )
                    return payload, response.status
        except AppointmentConflictError:
            raise
        except AppointmentRequestError:
            raise
        except asyncio.TimeoutError as exc:
            raise AppointmentBackendUnavailableError(
                "Java appointment service request timed out"
            ) from exc
        except aiohttp.ClientError as exc:
            raise AppointmentBackendUnavailableError(
                "Could not connect to Java appointment service"
            ) from exc

    async def _read_payload(self, response) -> Any:
        try:
            return await response.json(content_type=None)
        except (ValueError, aiohttp.ClientError) as exc:
            raise AppointmentBackendUnavailableError(
                "Java appointment service returned invalid JSON"
            ) from exc

    @staticmethod
    def _message(payload: Any, fallback: str) -> str:
        if isinstance(payload, dict) and payload.get("message"):
            return str(payload["message"])
        return fallback

    @staticmethod
    def _code(payload: Any, fallback: str) -> str:
        if isinstance(payload, dict) and payload.get("code"):
            return str(payload["code"])
        return fallback

    @staticmethod
    def _format_local_datetime(value: datetime) -> str:
        if value.tzinfo is not None:
            value = value.astimezone(time_config.BEIJING_TZ).replace(tzinfo=None)
        return value.isoformat(timespec="seconds")
