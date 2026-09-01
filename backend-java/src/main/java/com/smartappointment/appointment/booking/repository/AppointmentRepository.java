package com.smartappointment.appointment.booking.repository;

import com.smartappointment.appointment.booking.entity.Appointment;
import com.smartappointment.appointment.booking.entity.AppointmentStatus;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDateTime;
import java.util.Optional;

public interface AppointmentRepository extends JpaRepository<Appointment, Long> {

    Optional<Appointment> findByIdempotencyKey(String idempotencyKey);

    boolean existsByTechnicianIdAndStatusAndStartTimeLessThanAndEndTimeGreaterThan(
            long technicianId,
            AppointmentStatus status,
            LocalDateTime endTime,
            LocalDateTime startTime
    );
}
