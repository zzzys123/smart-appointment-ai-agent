package com.smartappointment.appointment.booking.service;

import com.smartappointment.appointment.booking.dto.CreateAppointmentRequest;
import com.smartappointment.appointment.booking.dto.CreateAppointmentResponse;
import com.smartappointment.appointment.booking.entity.Appointment;
import com.smartappointment.appointment.booking.exception.AppointmentSlotConflictException;
import com.smartappointment.appointment.booking.repository.AppointmentRepository;
import com.smartappointment.appointment.common.exception.InvalidRequestException;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

@Service
public class AppointmentCommandService {

    private final AppointmentRepository appointmentRepository;
    private final AppointmentTransactionService transactionService;

    public AppointmentCommandService(
            AppointmentRepository appointmentRepository,
            AppointmentTransactionService transactionService
    ) {
        this.appointmentRepository = appointmentRepository;
        this.transactionService = transactionService;
    }

    public AppointmentCreationResult createAppointment(
            CreateAppointmentRequest request,
            String idempotencyKey
    ) {
        String normalizedKey = normalizeIdempotencyKey(idempotencyKey);
        return appointmentRepository.findByIdempotencyKey(normalizedKey)
                .map(appointment -> result(appointment, false))
                .orElseGet(() -> createOrResolveConcurrentRequest(request, normalizedKey));
    }

    private AppointmentCreationResult createOrResolveConcurrentRequest(
            CreateAppointmentRequest request,
            String idempotencyKey
    ) {
        try {
            return result(transactionService.createNew(request, idempotencyKey), true);
        } catch (DataIntegrityViolationException exception) {
            return appointmentRepository.findByIdempotencyKey(idempotencyKey)
                    .map(appointment -> result(appointment, false))
                    .orElseThrow(() -> new AppointmentSlotConflictException(
                            "The requested technician time slot was booked concurrently"
                    ));
        }
    }

    private AppointmentCreationResult result(Appointment appointment, boolean created) {
        return new AppointmentCreationResult(CreateAppointmentResponse.from(appointment), created);
    }

    private String normalizeIdempotencyKey(String idempotencyKey) {
        if (!StringUtils.hasText(idempotencyKey)) {
            throw new InvalidRequestException(
                    "INVALID_IDEMPOTENCY_KEY",
                    "Idempotency-Key header must not be blank"
            );
        }
        String normalized = idempotencyKey.trim();
        if (normalized.length() > 128) {
            throw new InvalidRequestException(
                    "INVALID_IDEMPOTENCY_KEY",
                    "Idempotency-Key header must not exceed 128 characters"
            );
        }
        return normalized;
    }
}
