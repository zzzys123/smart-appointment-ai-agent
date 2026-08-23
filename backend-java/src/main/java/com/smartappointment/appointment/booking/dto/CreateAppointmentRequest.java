package com.smartappointment.appointment.booking.dto;

import jakarta.validation.constraints.Future;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

import java.time.LocalDateTime;

public record CreateAppointmentRequest(
        @NotBlank @Size(max = 64) String userId,
        @NotBlank @Size(max = 128) String sessionId,
        @Positive long technicianId,
        @NotBlank @Size(max = 128) String serviceName,
        @NotNull @Future LocalDateTime startTime,
        @Min(30) @Max(480) int durationMinutes
) {
}
