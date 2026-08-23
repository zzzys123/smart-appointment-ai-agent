import asyncio
from datetime import datetime

import pytest

from config.appointment_backend_config import AppointmentBackendConfig
from config.time_config import time_config
from services.appointment_gateway import (
    AppointmentBackendUnavailableError,
    AppointmentConflictError,
    AppointmentResult,
    CreateAppointmentCommand,
    JavaAppointmentGateway,
    LocalAppointmentGateway,
    create_appointment_gateway,
)
from services.appointment_gateway import java_gateway as java_gateway_module


pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self, content_type=None):
        return self.payload


class _FakeSession:
    def __init__(self, responses, calls, timeout=None):
        self.responses = responses
        self.calls = calls
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _install_fake_client(monkeypatch, responses):
    calls = []

    def client_session(timeout=None):
        return _FakeSession(responses, calls, timeout)

    monkeypatch.setattr(
        java_gateway_module.aiohttp,
        "ClientSession",
        client_session,
    )
    return calls


def _command():
    return CreateAppointmentCommand(
        user_id="user-1",
        session_id="session-1",
        technician_id=2,
        service_name="肩颈按摩",
        start_time=datetime(
            2030, 1, 2, 14, 0, tzinfo=time_config.BEIJING_TZ
        ),
        duration_minutes=60,
        trace_id="trace-stage4-001",
    )


@pytest.mark.asyncio
async def test_java_gateway_maps_query_and_create_contract(monkeypatch):
    technician = {
        "id": 2,
        "name": "李娜",
        "gender": "女",
        "strength": "舒缓放松",
        "enabled": True,
    }
    calls = _install_fake_client(
        monkeypatch,
        [
            _FakeResponse(200, [technician]),
            _FakeResponse(200, [technician]),
            _FakeResponse(
                201,
                {
                    "code": "OK",
                    "message": "预约成功",
                    "data": {
                        "appointmentNo": "APT203001020001",
                        "status": "CONFIRMED",
                    },
                },
            ),
        ],
    )
    gateway = JavaAppointmentGateway("http://java-service:8080", 2.5)

    assert await gateway.list_technicians() == [technician]
    assert await gateway.find_available_technicians(
        _command().start_time, 60, gender="女"
    ) == [technician]
    result = await gateway.create_appointment(_command(), "idem-1")

    assert result == AppointmentResult(
        appointment_no="APT203001020001",
        status="CONFIRMED",
        created=True,
    )
    assert calls[1][2]["params"] == {
        "startTime": "2030-01-02T14:00:00",
        "durationMinutes": 60,
        "gender": "女",
    }
    assert calls[2][2]["headers"] == {
        "Idempotency-Key": "idem-1",
        "X-Trace-Id": "trace-stage4-001",
    }
    assert calls[2][2]["json"] == {
        "userId": "user-1",
        "sessionId": "session-1",
        "technicianId": 2,
        "serviceName": "肩颈按摩",
        "startTime": "2030-01-02T14:00:00",
        "durationMinutes": 60,
    }


@pytest.mark.asyncio
async def test_java_gateway_maps_replay_conflict_and_timeout(monkeypatch):
    calls = _install_fake_client(
        monkeypatch,
        [
            _FakeResponse(
                200,
                {
                    "code": "OK",
                    "data": {
                        "appointmentNo": "APT-ORIGINAL",
                        "status": "CONFIRMED",
                    },
                },
            ),
            _FakeResponse(
                409,
                {
                    "code": "APPOINTMENT_SLOT_CONFLICT",
                    "message": "slot occupied",
                },
            ),
            asyncio.TimeoutError(),
        ],
    )
    gateway = JavaAppointmentGateway("http://java-service:8080")

    replay = await gateway.create_appointment(_command(), "same-key")
    assert replay.created is False
    assert replay.appointment_no == "APT-ORIGINAL"

    with pytest.raises(AppointmentConflictError) as conflict:
        await gateway.create_appointment(_command(), "different-key")
    assert conflict.value.code == "APPOINTMENT_SLOT_CONFLICT"

    with pytest.raises(AppointmentBackendUnavailableError):
        await gateway.list_technicians()
    assert len(calls) == 3


class _LocalServiceStub:
    def __init__(self, save_success=True, available=True):
        self.save_success = save_success
        self.available = available
        self.saved_args = None
        self.technicians = [
            {"id": 2, "name": "李娜", "gender": "女", "strength": "舒缓放松"},
            {"id": 3, "name": "王强", "gender": "男", "strength": "深层按摩"},
        ]

    def get_all_technicians(self):
        return self.technicians

    def is_technician_available(self, technician_id, start_time, end_time):
        return self.available

    def save_appointment(self, *args):
        self.saved_args = args
        return self.save_success


@pytest.mark.asyncio
async def test_local_gateway_implements_same_contract():
    service = _LocalServiceStub()
    gateway = LocalAppointmentGateway(service)

    available = await gateway.find_available_technicians(
        _command().start_time,
        60,
        gender="女",
    )
    result = await gateway.create_appointment(_command(), "local-idem")

    assert [tech["name"] for tech in available] == ["李娜"]
    assert result.status == "CONFIRMED"
    assert service.saved_args[-1] == "local-idem"


def test_gateway_factory_switches_backend():
    local = create_appointment_gateway(
        AppointmentBackendConfig(backend="local")
    )
    java = create_appointment_gateway(
        AppointmentBackendConfig(
            backend="java",
            java_base_url="http://java-service:8080",
            timeout_seconds=1,
        )
    )

    assert isinstance(local, LocalAppointmentGateway)
    assert isinstance(java, JavaAppointmentGateway)
