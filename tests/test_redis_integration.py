from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4
import asyncio

from config.redis_config import redis_config
from services.appointment_service import AppointmentService
from services.chat_session_service import resolve_session_id
from services.redis_service import RedisService


def test_resolve_session_id_accepts_safe_client_id():
    session_id = str(uuid4())
    assert resolve_session_id(session_id) == session_id


def test_resolve_session_id_rejects_unsafe_value():
    session_id = resolve_session_id("../../redis:key")
    assert session_id != "../../redis:key"
    assert len(session_id) == 36


def test_disabled_redis_uses_local_chat_lock():
    async def exercise_lock():
        service = RedisService(replace(redis_config, enabled=False))
        async with service.chat_lock("session_123456789") as acquired:
            assert acquired is True

    asyncio.run(exercise_lock())


def test_appointment_write_is_idempotent_and_rechecks_conflicts(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'appointments.db'}"
    service = AppointmentService(database_url)
    technician_id = service.add_technician("并发测试技师", "女", "轻柔")
    start = datetime(2030, 1, 2, 14, 0)
    end = start + timedelta(hours=1)
    session_id = str(uuid4())

    assert service.save_appointment(
        technician_id, start, end, {}, session_id
    ) is True
    assert service.save_appointment(
        technician_id, start, end, {}, session_id
    ) is True
    assert service.save_appointment(
        technician_id,
        start + timedelta(minutes=30),
        end + timedelta(minutes=30),
        {},
        str(uuid4()),
    ) is False

    schedules = service.get_technician_schedules(technician_id, start)
    assert len(schedules) == 1


def test_concurrent_overlapping_appointments_only_create_one(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'concurrent.db'}"
    setup_service = AppointmentService(database_url)
    technician_id = setup_service.add_technician("锁测试技师", "男", "力度大")
    start = datetime(2030, 2, 3, 10, 0)
    end = start + timedelta(hours=1)

    def book(session_id):
        service = AppointmentService(database_url)
        return service.save_appointment(
            technician_id, start, end, {}, session_id
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(book, [str(uuid4()), str(uuid4())]))

    assert sorted(results) == [False, True]
    assert len(setup_service.get_technician_schedules(technician_id, start)) == 1
