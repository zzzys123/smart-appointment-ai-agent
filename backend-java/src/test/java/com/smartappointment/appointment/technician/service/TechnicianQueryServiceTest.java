package com.smartappointment.appointment.technician.service;

import com.smartappointment.appointment.common.exception.InvalidRequestException;
import com.smartappointment.appointment.technician.repository.TechnicianRepository;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;

class TechnicianQueryServiceTest {

    private final TechnicianRepository technicianRepository = mock(TechnicianRepository.class);
    private final TechnicianQueryService service = new TechnicianQueryService(technicianRepository);

    @Test
    void rejectsDurationThatIsNotAMultipleOfThirtyMinutes() {
        assertThatThrownBy(() -> service.findAvailableTechnicians(
                LocalDateTime.of(2026, 8, 24, 14, 0),
                45,
                null,
                null
        ))
                .isInstanceOf(InvalidRequestException.class)
                .hasMessage("durationMinutes must be a multiple of 30");

        verifyNoInteractions(technicianRepository);
    }
}
