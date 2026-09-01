package com.smartappointment.appointment.technician.repository;

import com.smartappointment.appointment.technician.entity.TechnicianSchedule;
import com.smartappointment.appointment.technician.entity.ScheduleStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;

public interface TechnicianScheduleRepository extends JpaRepository<TechnicianSchedule, Long> {

    @Query("""
            select (count(s) > 0)
            from TechnicianSchedule s
            where s.technician.id = :technicianId
              and s.status = :status
              and s.startTime < :endTime
              and s.endTime > :startTime
            """)
    boolean existsBusyOverlap(
            @Param("technicianId") long technicianId,
            @Param("status") ScheduleStatus status,
            @Param("startTime") LocalDateTime startTime,
            @Param("endTime") LocalDateTime endTime
    );
}
