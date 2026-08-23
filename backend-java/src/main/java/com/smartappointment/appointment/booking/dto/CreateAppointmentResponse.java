package com.smartappointment.appointment.booking.dto;

import com.smartappointment.appointment.booking.entity.Appointment;
import com.smartappointment.appointment.booking.entity.AppointmentStatus;

public record CreateAppointmentResponse(
        String appointmentNo,
        AppointmentStatus status
) {
    public static CreateAppointmentResponse from(Appointment appointment) {
        return new CreateAppointmentResponse(
                appointment.getAppointmentNo(),
                appointment.getStatus()
        );
    }
}
