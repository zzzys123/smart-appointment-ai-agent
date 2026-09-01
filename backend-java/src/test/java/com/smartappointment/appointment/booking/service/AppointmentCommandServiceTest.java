package com.smartappointment.appointment.booking.service;

import com.smartappointment.appointment.booking.dto.CreateAppointmentRequest;
import com.smartappointment.appointment.booking.entity.Appointment;
import com.smartappointment.appointment.booking.repository.AppointmentRepository;
import com.smartappointment.appointment.common.exception.InvalidRequestException;
import com.smartappointment.appointment.technician.entity.Technician;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class AppointmentCommandServiceTest {

    private final AppointmentRepository appointmentRepository = mock(AppointmentRepository.class);
    private final AppointmentTransactionService transactionService = mock(AppointmentTransactionService.class);
    private final AppointmentCommandService service = new AppointmentCommandService(
            appointmentRepository,
            transactionService
    );

    @Test
    void returnsExistingAppointmentWithoutStartingAnotherTransaction() {
        Appointment existing = appointment("idempotency-key");
        when(appointmentRepository.findByIdempotencyKey("idempotency-key"))
                .thenReturn(Optional.of(existing));

        AppointmentCreationResult result = service.createAppointment(request(), " idempotency-key ");

        assertThat(result.created()).isFalse();
        assertThat(result.response().appointmentNo()).isEqualTo("APT202608240001");
        verifyNoInteractions(transactionService);
    }

    @Test
    void rejectsBlankIdempotencyKey() {
        assertThatThrownBy(() -> service.createAppointment(request(), "  "))
                .isInstanceOf(InvalidRequestException.class)
                .hasMessage("Idempotency-Key header must not be blank");

        verifyNoInteractions(appointmentRepository, transactionService);
    }

    private CreateAppointmentRequest request() {
        return new CreateAppointmentRequest(
                "default_user",
                "session-123",
                1,
                "肩颈按摩",
                LocalDateTime.now().plusDays(2).withHour(14).withMinute(0).withSecond(0).withNano(0),
                60
        );
    }

    private Appointment appointment(String idempotencyKey) {
        return new Appointment(
                "APT202608240001",
                "default_user",
                "session-123",
                new Technician("张伟", "男", "深层组织按摩", true),
                "肩颈按摩",
                LocalDateTime.now().plusDays(2).withHour(14).withMinute(0).withSecond(0).withNano(0),
                LocalDateTime.now().plusDays(2).withHour(15).withMinute(0).withSecond(0).withNano(0),
                idempotencyKey
        );
    }
}
