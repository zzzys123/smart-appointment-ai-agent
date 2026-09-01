"""Agent 与具体预约后端之间的稳定契约。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol


TechnicianData = Dict[str, Any]


@dataclass(frozen=True)
class CreateAppointmentCommand:
    user_id: str
    session_id: str
    technician_id: int
    service_name: str
    start_time: datetime
    duration_minutes: int
    trace_id: Optional[str] = None


@dataclass(frozen=True)
class AppointmentResult:
    appointment_no: Optional[str]
    status: str
    created: bool


class AppointmentGatewayError(RuntimeError):
    """所有预约网关错误的基类。"""

    def __init__(self, message: str, code: str = "APPOINTMENT_GATEWAY_ERROR"):
        super().__init__(message)
        self.code = code


class AppointmentConflictError(AppointmentGatewayError):
    """技师时间段已经被其他请求占用。"""

    def __init__(self, message: str = "The requested appointment slot is unavailable"):
        super().__init__(message, "APPOINTMENT_SLOT_CONFLICT")


class AppointmentBackendUnavailableError(AppointmentGatewayError):
    """预约后端超时、连接失败或返回不可用响应。"""

    def __init__(self, message: str = "Appointment backend is unavailable"):
        super().__init__(message, "APPOINTMENT_BACKEND_UNAVAILABLE")


class AppointmentRequestError(AppointmentGatewayError):
    """预约参数未通过后端业务校验。"""


class AppointmentGateway(Protocol):
    async def list_technicians(self) -> List[TechnicianData]: ...

    async def find_available_technicians(
        self,
        start_time: datetime,
        duration_minutes: int,
        gender: Optional[str] = None,
        strength: Optional[str] = None,
    ) -> List[TechnicianData]: ...

    async def create_appointment(
        self,
        command: CreateAppointmentCommand,
        idempotency_key: str,
    ) -> AppointmentResult: ...
