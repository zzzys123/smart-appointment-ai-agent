"""根据环境变量选择预约后端实现。"""

from __future__ import annotations

from typing import Optional

from config.appointment_backend_config import AppointmentBackendConfig

from .base import AppointmentGateway
from .java_gateway import JavaAppointmentGateway
from .local_gateway import LocalAppointmentGateway


def create_appointment_gateway(
    config: Optional[AppointmentBackendConfig] = None,
) -> AppointmentGateway:
    selected = config or AppointmentBackendConfig.from_env()
    if selected.backend == "java":
        return JavaAppointmentGateway(
            base_url=selected.java_base_url,
            timeout_seconds=selected.timeout_seconds,
        )
    return LocalAppointmentGateway()
