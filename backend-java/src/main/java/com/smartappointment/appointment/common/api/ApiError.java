package com.smartappointment.appointment.common.api;

import java.time.OffsetDateTime;

public record ApiError(
        String code,
        String message,
        String path,
        OffsetDateTime timestamp
) {
}
