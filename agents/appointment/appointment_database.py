"""
预约数据库操作器

负责处理预约相关的数据库操作
注意：现在通过Services层访问数据库，符合分层架构
"""

import hashlib
from typing import Dict, Any, Optional
from datetime import datetime
from uuid import uuid4
from config.time_config import time_config
from config.constants import busy_periods_dict
from services.appointment_gateway import (
    AppointmentGateway,
    AppointmentResult,
    CreateAppointmentCommand,
    create_appointment_gateway,
)


class AppointmentDatabase:
    """预约数据库操作器"""
    
    def __init__(self, appointment_gateway: Optional[AppointmentGateway] = None):
        self.appointment_gateway = (
            appointment_gateway or create_appointment_gateway()
        )
        self._user_behavior_service = None
    
    @property 
    def user_behavior_service(self):
        """懒加载用户行为服务"""
        if self._user_behavior_service is None:
            from services.user_behavior_service import UserBehaviorService
            self._user_behavior_service = UserBehaviorService()
        return self._user_behavior_service
    
    async def save_appointment(self, technician_id: str, start_time: datetime,
                              end_time: datetime, appointment_history: Dict[str, Any],
                              session_id: str) -> AppointmentResult:
        """通过所选网关保存预约；业务错误由上层转换为用户提示。"""
        technician_id_int = int(technician_id)
        duration_minutes = int((end_time - start_time).total_seconds() / 60)
        idempotency_key = self._build_idempotency_key(
            session_id,
            technician_id_int,
            start_time,
            end_time,
        )
        result = await self.appointment_gateway.create_appointment(
            CreateAppointmentCommand(
                user_id="default_user",
                session_id=session_id,
                technician_id=technician_id_int,
                service_name=appointment_history.get("project") or "massage",
                start_time=start_time,
                duration_minutes=duration_minutes,
                trace_id=uuid4().hex,
            ),
            idempotency_key,
        )

        if result.created:
            self._record_user_behavior(
                start_time,
                end_time,
                str(technician_id_int),
                appointment_history,
                session_id,
            )
        return result

    @staticmethod
    def _build_idempotency_key(
        session_id: str,
        technician_id: int,
        start_time: datetime,
        end_time: datetime,
    ) -> str:
        source = (
            f"{session_id}|{technician_id}|"
            f"{start_time.isoformat()}|{end_time.isoformat()}"
        )
        return hashlib.sha256(source.encode("utf-8")).hexdigest()
    
    def update_memory_schedule(self, technician_id: str, start_time: datetime, end_time: datetime):
        """更新内存中的技师忙碌时间段"""
        busy_period = {
            "start": time_config.format_datetime(start_time, "%H:%M"),
            "end": time_config.format_datetime(end_time, "%H:%M")
        }
        busy_periods_dict.setdefault(technician_id, []).append(busy_period)
    
    def _record_user_behavior(self, start_time: datetime, end_time: datetime,
                            technician_id: str, appointment_history: Dict[str, Any], 
                            session_id: str):
        """记录用户预约行为"""
        try:
            action_data = {
                'start_time': time_config.format_datetime(start_time, "%Y-%m-%d %H:%M:%S"),
                'end_time': time_config.format_datetime(end_time, "%Y-%m-%d %H:%M:%S"),
                'duration': int((end_time - start_time).total_seconds() / 60),
                'project': appointment_history.get('project', 'massage'),
                'preference': appointment_history.get('preference', ''),
                'technician_id': technician_id
            }
            
            # 通过Services层记录用户行为
            self.user_behavior_service.record_behavior(
                user_id="default_user",  # 统一使用default_user作为用户ID
                action_type='appointment',
                action_data=action_data,
                technician_id=str(technician_id),
                session_id=session_id
            )
            
        except Exception as behavior_error:
            print(f"记录用户行为失败（但预约仍然成功）：{behavior_error}")
