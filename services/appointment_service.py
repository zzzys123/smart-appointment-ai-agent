"""
预约服务层

职责：
1. 封装预约相关的数据库操作
2. 处理预约业务逻辑
3. 提供预约相关的数据服务
"""

import hashlib
import time
from typing import Dict, Any, List, Optional
from datetime import datetime
from db.db_router import DatabaseRouter
import logging

logger = logging.getLogger(__name__)

class AppointmentService:
    """预约服务类"""
    
    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db'):
        self.db_router = DatabaseRouter(db_path)
        self.technician_repo = self.db_router.technicians
    
    def save_appointment(self, technician_id: str, start_time: datetime,
                        end_time: datetime, appointment_history: Dict[str, Any],
                        session_id: str, idempotency_key: Optional[str] = None) -> bool:
        """保存预约信息到数据库"""
        try:
            from services.redis_service import get_redis_service

            technician_id_int = int(technician_id)
            if not idempotency_key:
                idempotency_source = (
                    f"{session_id}|{technician_id_int}|"
                    f"{start_time.isoformat()}|{end_time.isoformat()}"
                )
                idempotency_key = hashlib.sha256(
                    idempotency_source.encode("utf-8")
                ).hexdigest()
            redis_service = get_redis_service()

            # Serialize all writes for one technician. Availability is checked
            # again inside the lock to close the check-then-insert race.
            with redis_service.appointment_lock(
                technician_id_int, "schedule"
            ) as acquired:
                if not acquired:
                    logger.warning("预约锁获取超时：技师ID=%s", technician_id_int)
                    return False
                if redis_service.is_idempotent_appointment(idempotency_key):
                    logger.info("忽略重复预约提交：%s", idempotency_key)
                    return True
                if not self.technician_repo.is_technician_available(
                    technician_id_int, start_time, end_time
                ):
                    logger.warning(
                        "预约时间已被占用：技师ID=%s, 时间=%s 到 %s",
                        technician_id_int,
                        start_time,
                        end_time,
                    )
                    return False

                appointment_id = int(time.time() * 1000)
                self.technician_repo.add_schedule(
                    technician_id=technician_id_int,
                    start_time=start_time,
                    end_time=end_time,
                    status="busy",
                    appointment_id=appointment_id,
                )
                redis_service.mark_idempotent_appointment(idempotency_key)
            
            logger.info(f"预约信息已保存到数据库：技师ID={technician_id}, 时间={start_time} 到 {end_time}, 预约ID={appointment_id}")
            return True
            
        except Exception as e:
            logger.error(f"保存预约信息到数据库失败：{e}")
            return False
    
    def get_technician_by_id(self, technician_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取技师信息"""
        try:
            return self.technician_repo.get_technician_by_id(technician_id)
        except Exception as e:
            logger.error(f"获取技师信息失败：{e}")
            return None
    
    def get_technician_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """根据姓名获取技师信息"""
        try:
            return self.technician_repo.get_technician_by_name(name)
        except Exception as e:
            logger.error(f"获取技师信息失败：{e}")
            return None
    
    def get_all_technicians(self) -> List[Dict[str, Any]]:
        """获取所有技师信息"""
        try:
            return self.technician_repo.get_all_technicians()
        except Exception as e:
            logger.error(f"获取技师列表失败：{e}")
            return []
    
    def get_technicians_by_gender(self, gender: str) -> List[Dict[str, Any]]:
        """根据性别获取技师信息"""
        try:
            return self.technician_repo.get_technicians_by_gender(gender)
        except Exception as e:
            logger.error(f"根据性别获取技师信息失败：{e}")
            return []
    
    def get_technician_schedules(self, technician_id: int, date) -> List[Dict[str, Any]]:
        """获取技师排班信息"""
        try:
            return self.technician_repo.get_technician_schedules(technician_id, date)
        except Exception as e:
            logger.error(f"获取技师排班信息失败：{e}")
            return []
    
    def is_technician_available(self, technician_id: int, start_time: datetime, end_time: datetime) -> bool:
        """检查技师是否可用"""
        try:
            return self.technician_repo.is_technician_available(technician_id, start_time, end_time)
        except Exception as e:
            logger.error(f"检查技师可用性失败：{e}")
            return False
    
    def add_technician(self, name: str, gender: str = None, strength: str = None) -> Optional[int]:
        """添加新技师"""
        try:
            return self.technician_repo.add_technician(name, gender, strength)
        except Exception as e:
            logger.error(f"添加技师失败：{e}")
            return None
    
    def get_all_strengths(self) -> List[str]:
        """获取所有技师的专长列表"""
        try:
            return self.technician_repo.get_all_strengths()
        except Exception as e:
            logger.error(f"获取技师专长列表失败：{e}")
            return []
