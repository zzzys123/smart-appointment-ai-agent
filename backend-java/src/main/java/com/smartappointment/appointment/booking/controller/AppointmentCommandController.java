package com.smartappointment.appointment.booking.controller;

import com.smartappointment.appointment.booking.dto.CreateAppointmentRequest;
import com.smartappointment.appointment.booking.dto.CreateAppointmentResponse;
import com.smartappointment.appointment.booking.exception.AppointmentSlotConflictException;
import com.smartappointment.appointment.booking.service.AppointmentCommandService;
import com.smartappointment.appointment.booking.service.AppointmentCreationResult;
import com.smartappointment.appointment.common.api.ApiResponse;
import io.swagger.v3.oas.annotations.Operation;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

@RestController
@RequestMapping("/internal/v1/appointments")
public class AppointmentCommandController {

    private static final Logger logger = LoggerFactory.getLogger(AppointmentCommandController.class);

    private final AppointmentCommandService appointmentCommandService;

    public AppointmentCommandController(AppointmentCommandService appointmentCommandService) {
        this.appointmentCommandService = appointmentCommandService;
    }

    @Operation(summary = "Create an idempotent appointment with database-backed slot protection")
    @PostMapping
    public ResponseEntity<ApiResponse<CreateAppointmentResponse>> createAppointment(
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @RequestHeader(value = "X-Trace-Id", required = false) String traceId,
            @Valid @RequestBody CreateAppointmentRequest request
    ) {
        String effectiveTraceId = normalizeTraceId(traceId);
        try {
            AppointmentCreationResult result = appointmentCommandService.createAppointment(
                    request,
                    idempotencyKey
            );
            HttpStatus status = result.created() ? HttpStatus.CREATED : HttpStatus.OK;
            logger.info(
                    "appointment_create_result traceId={} sessionId={} technicianId={} appointmentNo={} outcome={}",
                    effectiveTraceId,
                    request.sessionId(),
                    request.technicianId(),
                    result.response().appointmentNo(),
                    result.created() ? "created" : "replayed"
            );
            return ResponseEntity.status(status)
                    .header("X-Trace-Id", effectiveTraceId)
                    .body(new ApiResponse<>(
                            "OK",
                            result.created() ? "预约成功" : "重复请求，返回原预约结果",
                            result.response()
                    ));
        } catch (AppointmentSlotConflictException exception) {
            logger.warn(
                    "appointment_create_conflict traceId={} sessionId={} technicianId={} reason={}",
                    effectiveTraceId,
                    request.sessionId(),
                    request.technicianId(),
                    exception.getMessage()
            );
            throw exception;
        }
    }

    private String normalizeTraceId(String traceId) {
        if (traceId == null || traceId.isBlank()) {
            return UUID.randomUUID().toString().replace("-", "");
        }
        String sanitized = traceId.replace("\r", "").replace("\n", "").trim();
        return sanitized.substring(0, Math.min(sanitized.length(), 64));
    }
}
