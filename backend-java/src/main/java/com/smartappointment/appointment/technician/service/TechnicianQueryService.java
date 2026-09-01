package com.smartappointment.appointment.technician.service;

import com.smartappointment.appointment.booking.entity.AppointmentStatus;
import com.smartappointment.appointment.common.exception.InvalidRequestException;
import com.smartappointment.appointment.common.exception.ResourceNotFoundException;
import com.smartappointment.appointment.technician.dto.TechnicianResponse;
import com.smartappointment.appointment.technician.entity.ScheduleStatus;
import com.smartappointment.appointment.technician.repository.TechnicianRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.time.LocalDateTime;
import java.util.List;

@Service
@Transactional(readOnly = true)
public class TechnicianQueryService {

    private static final int SLOT_MINUTES = 30;

    private final TechnicianRepository technicianRepository;

    public TechnicianQueryService(TechnicianRepository technicianRepository) {
        this.technicianRepository = technicianRepository;
    }

    public List<TechnicianResponse> listEnabledTechnicians() {
        return technicianRepository.findAllByEnabledTrueOrderByIdAsc().stream()
                .map(TechnicianResponse::from)
                .toList();
    }

    public TechnicianResponse getEnabledTechnician(long technicianId) {
        return technicianRepository.findById(technicianId)
                .filter(technician -> technician.isEnabled())
                .map(TechnicianResponse::from)
                .orElseThrow(() -> new ResourceNotFoundException(
                        "TECHNICIAN_NOT_FOUND",
                        "Technician %d does not exist or is disabled".formatted(technicianId)
                ));
    }

    public List<TechnicianResponse> findAvailableTechnicians(
            LocalDateTime startTime,
            int durationMinutes,
            String gender,
            String strength
    ) {
        if (durationMinutes % SLOT_MINUTES != 0) {
            throw new InvalidRequestException(
                    "INVALID_DURATION",
                    "durationMinutes must be a multiple of 30"
            );
        }

        LocalDateTime endTime = startTime.plusMinutes(durationMinutes);
        return technicianRepository.findAvailableTechnicians(
                        startTime,
                        endTime,
                        normalize(gender),
                        normalize(strength),
                        ScheduleStatus.BUSY,
                        AppointmentStatus.CONFIRMED
                ).stream()
                .map(TechnicianResponse::from)
                .toList();
    }

    private String normalize(String value) {
        return StringUtils.hasText(value) ? value.trim() : null;
    }
}
