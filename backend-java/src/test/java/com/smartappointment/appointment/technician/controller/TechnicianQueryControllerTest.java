package com.smartappointment.appointment.technician.controller;

import com.smartappointment.appointment.common.exception.ResourceNotFoundException;
import com.smartappointment.appointment.technician.dto.TechnicianResponse;
import com.smartappointment.appointment.technician.service.TechnicianQueryService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDateTime;
import java.util.List;

import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(TechnicianQueryController.class)
class TechnicianQueryControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private TechnicianQueryService technicianQueryService;

    @Test
    void listsEnabledTechnicians() throws Exception {
        when(technicianQueryService.listEnabledTechnicians()).thenReturn(List.of(
                new TechnicianResponse(1L, "张伟", "男", "深层组织按摩", true)
        ));

        mockMvc.perform(get("/internal/v1/technicians"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").value(1))
                .andExpect(jsonPath("$[0].name").value("张伟"))
                .andExpect(jsonPath("$[0].enabled").value(true));
    }

    @Test
    void getsTechnicianById() throws Exception {
        when(technicianQueryService.getEnabledTechnician(1L)).thenReturn(
                new TechnicianResponse(1L, "张伟", "男", "深层组织按摩", true)
        );

        mockMvc.perform(get("/internal/v1/technicians/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.name").value("张伟"));
    }

    @Test
    void returnsStructuredNotFoundError() throws Exception {
        when(technicianQueryService.getEnabledTechnician(99L)).thenThrow(
                new ResourceNotFoundException("TECHNICIAN_NOT_FOUND", "Technician 99 does not exist")
        );

        mockMvc.perform(get("/internal/v1/technicians/99"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("TECHNICIAN_NOT_FOUND"))
                .andExpect(jsonPath("$.path").value("/internal/v1/technicians/99"));
    }

    @Test
    void queriesAvailableTechniciansWithFilters() throws Exception {
        LocalDateTime startTime = LocalDateTime.of(2026, 8, 24, 14, 0);
        when(technicianQueryService.findAvailableTechnicians(
                startTime,
                60,
                "女",
                "舒缓"
        )).thenReturn(List.of(
                new TechnicianResponse(3L, "李娜", "女", "舒缓放松", true)
        ));

        mockMvc.perform(get("/internal/v1/technicians/available")
                        .param("startTime", "2026-08-24T14:00:00")
                        .param("durationMinutes", "60")
                        .param("gender", "女")
                        .param("strength", "舒缓"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].name").value("李娜"));

        verify(technicianQueryService).findAvailableTechnicians(
                startTime,
                60,
                "女",
                "舒缓"
        );
    }

    @Test
    void rejectsDurationBelowMinimum() throws Exception {
        mockMvc.perform(get("/internal/v1/technicians/available")
                        .param("startTime", "2026-08-24T14:00:00")
                        .param("durationMinutes", "15"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_REQUEST"));
    }
}
