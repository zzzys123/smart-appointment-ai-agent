package com.smartappointment.appointment.technician.repository;

import com.smartappointment.appointment.booking.entity.AppointmentStatus;
import com.smartappointment.appointment.technician.entity.ScheduleStatus;
import com.smartappointment.appointment.technician.entity.Technician;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.List;

public interface TechnicianRepository extends JpaRepository<Technician, Long> {

    List<Technician> findAllByEnabledTrueOrderByIdAsc();

    @Query("""
            select t
            from Technician t
            where t.enabled = true
              and (:gender is null or t.gender = :gender)
              and (:strength is null or lower(t.strength) like lower(concat('%', :strength, '%')))
              and not exists (
                  select s.id
                  from TechnicianSchedule s
                  where s.technician = t
                    and s.status = :busyStatus
                    and s.startTime < :endTime
                    and s.endTime > :startTime
              )
              and not exists (
                  select a.id
                  from Appointment a
                  where a.technician = t
                    and a.status = :confirmedStatus
                    and a.startTime < :endTime
                    and a.endTime > :startTime
              )
            order by t.id asc
            """)
    List<Technician> findAvailableTechnicians(
            @Param("startTime") LocalDateTime startTime,
            @Param("endTime") LocalDateTime endTime,
            @Param("gender") String gender,
            @Param("strength") String strength,
            @Param("busyStatus") ScheduleStatus busyStatus,
            @Param("confirmedStatus") AppointmentStatus confirmedStatus
    );
}
