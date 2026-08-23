from datetime import datetime

import pytest

import agents.appointment_agent as appointment_agent_module
from agents.appointment.appointment_database import AppointmentDatabase
from agents.appointment.appointment_processor import AppointmentProcessor
from agents.appointment.message_builder import MessageBuilder
from agents.appointment.technician_finder import TechnicianFinder
from agents.appointment_agent import AppointmentAgent
from config.time_config import time_config
from services.appointment_gateway import (
    AppointmentBackendUnavailableError,
    AppointmentConflictError,
    AppointmentResult,
)


pytestmark = pytest.mark.integration


class _AgentGatewayStub:
    def __init__(self, query_error=None, create_error=None):
        self.query_error = query_error
        self.create_error = create_error
        self.created_command = None
        self.idempotency_key = None
        self.technician = {
            "id": 1,
            "name": "张伟",
            "gender": "男",
            "strength": "深层组织按摩",
            "enabled": True,
        }

    async def list_technicians(self):
        if self.query_error:
            raise self.query_error
        return [self.technician]

    async def find_available_technicians(
        self, start_time, duration_minutes, gender=None, strength=None
    ):
        if self.query_error:
            raise self.query_error
        return [self.technician]

    async def create_appointment(self, command, idempotency_key):
        self.created_command = command
        self.idempotency_key = idempotency_key
        if self.create_error:
            raise self.create_error
        return AppointmentResult(
            appointment_no="APT203001020001",
            status="CONFIRMED",
            created=False,
        )


def _processor(gateway):
    database = AppointmentDatabase(gateway)
    database._record_user_behavior = lambda *args, **kwargs: None
    return AppointmentProcessor(
        input_parser=None,
        technician_finder=TechnicianFinder(gateway),
        message_builder=MessageBuilder(),
        appointment_database=database,
        llm=None,
    )


def _history():
    return {
        "gender": "男",
        "start_time": "2030-01-02 14:00",
        "duration": "60分钟",
        "project": "肩颈按摩",
        "preference": None,
        "technician_name": "张伟",
    }


async def _collect(processor, history=None):
    tokens = []
    async for token in processor.handle_complete_appointment(
        history or _history(), "session-stage3"
    ):
        tokens.append(token)
    return "".join(tokens)


@pytest.mark.asyncio
async def test_agent_creates_appointment_through_gateway():
    gateway = _AgentGatewayStub()
    processor = _processor(gateway)
    output = await _collect(processor)

    assert "预约成功" in output
    assert gateway.created_command.technician_id == 1
    assert gateway.created_command.service_name == "肩颈按摩"
    assert gateway.created_command.start_time == datetime(
        2030, 1, 2, 14, 0, tzinfo=time_config.BEIJING_TZ
    )
    assert len(gateway.idempotency_key) == 64
    assert len(gateway.created_command.trace_id) == 32
    assert processor.last_appointment_completed is True


@pytest.mark.asyncio
async def test_agent_maps_write_conflict_without_false_success():
    gateway = _AgentGatewayStub(
        create_error=AppointmentConflictError("slot occupied")
    )
    processor = _processor(gateway)
    output = await _collect(processor)

    assert "刚刚被其他用户预约" in output
    assert "预约成功" not in output
    assert processor.last_appointment_completed is False


@pytest.mark.asyncio
async def test_agent_reports_java_backend_failure_without_local_fallback():
    gateway = _AgentGatewayStub(
        query_error=AppointmentBackendUnavailableError("connection refused")
    )
    processor = _processor(gateway)
    output = await _collect(processor)

    assert "预约服务暂时无法连接" in output
    assert "本次没有创建预约" in output
    assert "预约成功" not in output
    assert gateway.created_command is None
    assert processor.last_appointment_completed is False


@pytest.mark.asyncio
async def test_agent_keeps_appointment_state_after_java_conflict(monkeypatch):
    class _NoModel:
        def __bool__(self):
            return False

        def with_structured_output(self, schema):
            return self

    gateway = _AgentGatewayStub(
        create_error=AppointmentConflictError("slot occupied")
    )
    monkeypatch.setattr(
        appointment_agent_module,
        "create_chat_model",
        lambda **kwargs: _NoModel(),
    )
    agent = AppointmentAgent(
        session_id="session-stage3",
        appointment_gateway=gateway,
    )
    complete_data = {
        "gender": "男",
        "start_time": "2030-01-02 14:00",
        "duration": "60分钟",
        "project": "肩颈按摩",
        "technician_name": "张伟",
        "unrelated": False,
    }
    monkeypatch.setattr(
        agent.input_parser,
        "parse_stream",
        lambda user_input, chat_history: iter(["parsed"]),
    )
    monkeypatch.setattr(
        agent.input_parser,
        "parse_data",
        lambda content: complete_data,
    )

    output = "".join([token async for token in agent.run_stream("预约")])

    assert "刚刚被其他用户预约" in output
    assert agent.last_run_completed is False
    assert agent.appointment_history["start_time"] == "2030-01-02 14:00"
