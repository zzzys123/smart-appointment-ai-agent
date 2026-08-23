package com.smartappointment.appointment.booking.service;

import com.smartappointment.appointment.booking.dto.CreateAppointmentResponse;

public record AppointmentCreationResult(
        CreateAppointmentResponse response,
        boolean created
) {
}
