"""Deterministic integration tests for the current user-behavior service."""

import pytest

from agents.user_behavior.pattern_analyzer import PatternAnalyzer
from services.user_behavior_service import UserBehaviorService


pytestmark = pytest.mark.integration


@pytest.fixture
def behavior_service(tmp_path):
    database_path = tmp_path / "behavior.db"
    return UserBehaviorService(f"sqlite:///{database_path.as_posix()}")


def test_records_and_filters_user_behavior(behavior_service):
    behavior_service.record_behavior(
        "user-1",
        "appointment",
        {"project": "肩颈按摩", "duration": 60},
    )
    behavior_service.record_behavior("user-1", "consultation", {"topic": "适用人群"})

    appointments = behavior_service.get_user_behaviors("user-1", action_type="appointment")

    assert len(appointments) == 1
    assert appointments[0]["action_data"]["project"] == "肩颈按摩"


def test_preference_confidence_is_created_and_incremented(behavior_service):
    behavior_service.update_user_preference("user-2", "service", "按摩", confidence_score=2)
    behavior_service.update_user_preference("user-2", "service", "按摩", confidence_score=3)

    preferences = behavior_service.get_user_preferences("user-2")

    assert len(preferences) == 1
    assert preferences[0]["preference_value"] == "按摩"
    assert preferences[0]["confidence_score"] == 5


def test_pattern_analysis_uses_current_appointment_schema(behavior_service):
    behavior_service.record_behavior("user-3", "appointment", {"project": "肩颈按摩", "duration": 60})
    behavior_service.record_behavior("user-3", "appointment", {"project": "肩颈按摩", "duration": 45})
    behavior_service.record_behavior("user-3", "appointment", {"project": "足疗", "duration": 30})

    result = PatternAnalyzer(behavior_service).analyze_user_preferences("user-3")

    assert result["total_appointments"] == 3
    assert result["favorite_service"] == "肩颈按摩"
    assert result["favorite_duration"] in {30, 45, 60}


def test_service_pattern_summary_has_no_data_state(behavior_service):
    result = behavior_service.analyze_user_patterns("new-user")

    assert result["pattern"] == "no_data"


def test_service_pattern_summary_counts_appointments(behavior_service):
    behavior_service.record_behavior("user-4", "appointment", {"project": "推拿", "duration": 60})
    behavior_service.record_behavior("user-4", "appointment", {"project": "推拿", "duration": 30})
    behavior_service.record_behavior("user-4", "consultation", {"topic": "价格"})

    result = behavior_service.analyze_user_patterns("user-4")

    assert result["total_appointments"] == 2
    assert result["pattern"] == "occasional_user"
    assert result["preferred_technician"] is None


def test_recommendation_lifecycle(behavior_service):
    repository = behavior_service.user_behavior_repo
    recommendation_id = repository.create_recommendation(
        "user-5",
        "return_reminder",
        "距离上次服务已有一段时间，欢迎再次预约。",
    )

    pending = repository.get_pending_recommendations("user-5")

    assert [item["id"] for item in pending] == [recommendation_id]
    assert repository.mark_recommendation_sent(recommendation_id) is True
    assert repository.get_pending_recommendations("user-5") == []
