"""预约后端网关及其本地、Java 实现。"""

from .base import (
    AppointmentBackendUnavailableError,
    AppointmentConflictError,
    AppointmentGateway,
    AppointmentGatewayError,
    AppointmentRequestError,
    AppointmentResult,
    CreateAppointmentCommand,
)
from .factory import create_appointment_gateway
from .java_gateway import JavaAppointmentGateway
from .local_gateway import LocalAppointmentGateway

__all__ = [
    "AppointmentBackendUnavailableError",
    "AppointmentConflictError",
    "AppointmentGateway",
    "AppointmentGatewayError",
    "AppointmentRequestError",
    "AppointmentResult",
    "CreateAppointmentCommand",
    "JavaAppointmentGateway",
    "LocalAppointmentGateway",
    "create_appointment_gateway",
]
