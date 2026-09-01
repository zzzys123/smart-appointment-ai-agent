package com.smartappointment.appointment.common.api;

public record ApiResponse<T>(
        String code,
        String message,
        T data
) {
}
