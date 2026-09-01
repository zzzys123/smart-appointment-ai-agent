package com.smartappointment.appointment.technician.controller;

import com.smartappointment.appointment.technician.dto.TechnicianResponse;
import com.smartappointment.appointment.technician.service.TechnicianQueryService;
import io.swagger.v3.oas.annotations.Operation;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Positive;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDateTime;
import java.util.List;

@Validated
@RestController
@RequestMapping("/internal/v1/technicians")
public class TechnicianQueryController {

    private final TechnicianQueryService technicianQueryService;

    public TechnicianQueryController(TechnicianQueryService technicianQueryService) {
        this.technicianQueryService = technicianQueryService;
    }

    @Operation(summary = "List all enabled technicians")
    @GetMapping
    public List<TechnicianResponse> listTechnicians() {
        return technicianQueryService.listEnabledTechnicians();
    }

    @Operation(summary = "Get an enabled technician by ID")
    @GetMapping("/{technicianId}")
    public TechnicianResponse getTechnician(@PathVariable @Positive long technicianId) {
        return technicianQueryService.getEnabledTechnician(technicianId);
    }

    @Operation(summary = "Find technicians without a busy schedule overlapping the requested time")
    @GetMapping("/available")
    public List<TechnicianResponse> findAvailableTechnicians(
            @RequestParam
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            LocalDateTime startTime,
            @RequestParam
            @Min(30)
            @Max(480)
            int durationMinutes,
            @RequestParam(required = false)
            String gender,
            @RequestParam(required = false)
            String strength
    ) {
        return technicianQueryService.findAvailableTechnicians(
                startTime,
                durationMinutes,
                gender,
                strength
        );
    }
}
