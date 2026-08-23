"""复用原 SQLite 预约逻辑的本地网关。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import List, Optional

from services.appointment_service import AppointmentService

from .base import (
    AppointmentBackendUnavailableError,
    AppointmentConflictError,
    AppointmentResult,
    CreateAppointmentCommand,
    TechnicianData,
)


class LocalAppointmentGateway:
    def __init__(self, appointment_service: Optional[AppointmentService] = None):
        self._appointment_service = appointment_service or AppointmentService()

    async def list_technicians(self) -> List[TechnicianData]:
        return await asyncio.to_thread(
            self._appointment_service.get_all_technicians
        )

    async def find_available_technicians(
        self,
        start_time: datetime,
        duration_minutes: int,
        gender: Optional[str] = None,
        strength: Optional[str] = None,
    ) -> List[TechnicianData]:
        end_time = start_time + timedelta(minutes=duration_minutes)

        def query() -> List[TechnicianData]:
            technicians = self._appointment_service.get_all_technicians()
            result = []
            for technician in technicians:
                if gender and technician.get("gender") != gender:
                    continue
                if strength and strength.lower() not in str(
                    technician.get("strength", "")
                ).lower():
                    continue
                if self._appointment_service.is_technician_available(
                    technician["id"], start_time, end_time
                ):
                    result.append(technician)
            return result

        return await asyncio.to_thread(query)

    async def create_appointment(
        self,
        command: CreateAppointmentCommand,
        idempotency_key: str,
    ) -> AppointmentResult:
        end_time = command.start_time + timedelta(
            minutes=command.duration_minutes
        )
        success = await asyncio.to_thread(
            self._appointment_service.save_appointment,
            str(command.technician_id),
            command.start_time,
            end_time,
            {"project": command.service_name},
            command.session_id,
            idempotency_key,
        )
        if success:
            return AppointmentResult(
                appointment_no=None,
                status="CONFIRMED",
                created=True,
            )

        available = await asyncio.to_thread(
            self._appointment_service.is_technician_available,
            command.technician_id,
            command.start_time,
            end_time,
        )
        if not available:
            raise AppointmentConflictError()
        raise AppointmentBackendUnavailableError(
            "Local appointment backend could not save the appointment"
        )
