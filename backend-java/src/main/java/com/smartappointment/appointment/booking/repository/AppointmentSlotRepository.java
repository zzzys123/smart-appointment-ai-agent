package com.smartappointment.appointment.booking.repository;

import com.smartappointment.appointment.booking.entity.AppointmentSlot;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AppointmentSlotRepository extends JpaRepository<AppointmentSlot, Long> {
}
