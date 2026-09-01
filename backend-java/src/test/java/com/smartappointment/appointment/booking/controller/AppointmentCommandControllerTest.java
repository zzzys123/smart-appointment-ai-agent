package com.smartappointment.appointment.booking.controller;

import com.smartappointment.appointment.booking.dto.CreateAppointmentResponse;
import com.smartappointment.appointment.booking.entity.AppointmentStatus;
import com.smartappointment.appointment.booking.exception.AppointmentSlotConflictException;
import com.smartappointment.appointment.booking.service.AppointmentCommandService;
import com.smartappointment.appointment.booking.service.AppointmentCreationResult;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDateTime;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(AppointmentCommandController.class)
class AppointmentCommandControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private AppointmentCommandService appointmentCommandService;

    @Test
    void createsAppointmentAndReturnsStructuredSuccess() throws Exception {
        when(appointmentCommandService.createAppointment(any(), eq("session-1-request-1")))
                .thenReturn(new AppointmentCreationResult(
                        new CreateAppointmentResponse("APT202608240001", AppointmentStatus.CONFIRMED),
                        true
                ));

        mockMvc.perform(post("/internal/v1/appointments")
                        .header("Idempotency-Key", "session-1-request-1")
                        .header("X-Trace-Id", "trace-controller-1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(validRequestJson()))
                .andExpect(status().isCreated())
                .andExpect(header().string("X-Trace-Id", "trace-controller-1"))
                .andExpect(jsonPath("$.code").value("OK"))
                .andExpect(jsonPath("$.message").value("预约成功"))
                .andExpect(jsonPath("$.data.appointmentNo").value("APT202608240001"))
                .andExpect(jsonPath("$.data.status").value("CONFIRMED"));
    }

    @Test
    void returnsOriginalAppointmentForIdempotentReplay() throws Exception {
        when(appointmentCommandService.createAppointment(any(), eq("session-1-request-1")))
                .thenReturn(new AppointmentCreationResult(
                        new CreateAppointmentResponse("APT202608240001", AppointmentStatus.CONFIRMED),
                        false
                ));

        mockMvc.perform(post("/internal/v1/appointments")
                        .header("Idempotency-Key", "session-1-request-1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(validRequestJson()))
                .andExpect(status().isOk())
                .andExpect(header().exists("X-Trace-Id"))
                .andExpect(jsonPath("$.code").value("OK"))
                .andExpect(jsonPath("$.data.appointmentNo").value("APT202608240001"));
    }

    @Test
    void returnsStructuredConflict() throws Exception {
        when(appointmentCommandService.createAppointment(any(), eq("session-2-request-1")))
                .thenThrow(new AppointmentSlotConflictException("Time slot is unavailable"));

        mockMvc.perform(post("/internal/v1/appointments")
                        .header("Idempotency-Key", "session-2-request-1")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(validRequestJson()))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("APPOINTMENT_SLOT_CONFLICT"))
                .andExpect(jsonPath("$.path").value("/internal/v1/appointments"));
    }

    @Test
    void rejectsPastStartTimeBeforeCallingService() throws Exception {
        String past = LocalDateTime.now().minusDays(1).withSecond(0).withNano(0).toString();

        mockMvc.perform(post("/internal/v1/appointments")
                        .header("Idempotency-Key", "past-request")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(requestJson(past)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_REQUEST"));
    }

    private String validRequestJson() {
        String future = LocalDateTime.now()
                .plusDays(2)
                .withHour(14)
                .withMinute(0)
                .withSecond(0)
                .withNano(0)
                .toString();
        return requestJson(future);
    }

    private String requestJson(String startTime) {
        return """
                {
                  "userId": "default_user",
                  "sessionId": "session-123",
                  "technicianId": 1,
                  "serviceName": "肩颈按摩",
                  "startTime": "%s",
                  "durationMinutes": 60
                }
                """.formatted(startTime);
    }
}
