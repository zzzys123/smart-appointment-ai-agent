package com.smartappointment.appointment.booking.service;

import com.smartappointment.appointment.booking.dto.CreateAppointmentRequest;
import com.smartappointment.appointment.booking.entity.Appointment;
import com.smartappointment.appointment.booking.entity.AppointmentSlot;
import com.smartappointment.appointment.booking.entity.AppointmentStatus;
import com.smartappointment.appointment.booking.exception.AppointmentSlotConflictException;
import com.smartappointment.appointment.booking.repository.AppointmentRepository;
import com.smartappointment.appointment.booking.repository.AppointmentSlotRepository;
import com.smartappointment.appointment.common.exception.InvalidRequestException;
import com.smartappointment.appointment.common.exception.ResourceNotFoundException;
import com.smartappointment.appointment.technician.entity.ScheduleStatus;
import com.smartappointment.appointment.technician.entity.Technician;
import com.smartappointment.appointment.technician.repository.TechnicianRepository;
import com.smartappointment.appointment.technician.repository.TechnicianScheduleRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

@Service
public class AppointmentTransactionService {

    static final int SLOT_MINUTES = 30;

    private final AppointmentRepository appointmentRepository;
    private final AppointmentSlotRepository appointmentSlotRepository;
    private final TechnicianRepository technicianRepository;
    private final TechnicianScheduleRepository technicianScheduleRepository;

    public AppointmentTransactionService(
            AppointmentRepository appointmentRepository,
            AppointmentSlotRepository appointmentSlotRepository,
            TechnicianRepository technicianRepository,
            TechnicianScheduleRepository technicianScheduleRepository
    ) {
        this.appointmentRepository = appointmentRepository;
        this.appointmentSlotRepository = appointmentSlotRepository;
        this.technicianRepository = technicianRepository;
        this.technicianScheduleRepository = technicianScheduleRepository;
    }

    @Transactional
    public Appointment createNew(CreateAppointmentRequest request, String idempotencyKey) {
        validateRequest(request);

        Technician technician = technicianRepository.findById(request.technicianId())
                .filter(Technician::isEnabled)
                .orElseThrow(() -> new ResourceNotFoundException(
                        "TECHNICIAN_NOT_FOUND",
                        "Technician %d does not exist or is disabled".formatted(request.technicianId())
                ));

        LocalDateTime endTime = request.startTime().plusMinutes(request.durationMinutes());
        rejectKnownConflicts(technician.getId(), request.startTime(), endTime);

        Appointment appointment = appointmentRepository.saveAndFlush(new Appointment(
                nextAppointmentNumber(request.startTime()),
                request.userId().trim(),
                request.sessionId().trim(),
                technician,
                request.serviceName().trim(),
                request.startTime(),
                endTime,
                idempotencyKey
        ));

        List<AppointmentSlot> slots = new ArrayList<>();
        for (LocalDateTime slotStart = request.startTime();
             slotStart.isBefore(endTime);
             slotStart = slotStart.plusMinutes(SLOT_MINUTES)) {
            slots.add(new AppointmentSlot(appointment, technician, slotStart));
        }
        appointmentSlotRepository.saveAllAndFlush(slots);
        return appointment;
    }

    private void validateRequest(CreateAppointmentRequest request) {
        if (!request.startTime().isAfter(LocalDateTime.now())) {
            throw new InvalidRequestException("INVALID_START_TIME", "startTime must be in the future");
        }
        if (request.durationMinutes() < SLOT_MINUTES
                || request.durationMinutes() > 480
                || request.durationMinutes() % SLOT_MINUTES != 0) {
            throw new InvalidRequestException(
                    "INVALID_DURATION",
                    "durationMinutes must be between 30 and 480 and a multiple of 30"
            );
        }
        if (request.startTime().getMinute() % SLOT_MINUTES != 0
                || request.startTime().getSecond() != 0
                || request.startTime().getNano() != 0) {
            throw new InvalidRequestException(
                    "INVALID_START_TIME",
                    "startTime must align to a 30-minute slot"
            );
        }
    }

    private void rejectKnownConflicts(long technicianId, LocalDateTime startTime, LocalDateTime endTime) {
        boolean appointmentConflict = appointmentRepository
                .existsByTechnicianIdAndStatusAndStartTimeLessThanAndEndTimeGreaterThan(
                        technicianId,
                        AppointmentStatus.CONFIRMED,
                        endTime,
                        startTime
                );
        boolean scheduleConflict = technicianScheduleRepository.existsBusyOverlap(
                technicianId,
                ScheduleStatus.BUSY,
                startTime,
                endTime
        );
        if (appointmentConflict || scheduleConflict) {
            throw new AppointmentSlotConflictException(
                    "Technician %d is not available for the requested time".formatted(technicianId)
            );
        }
    }

    private String nextAppointmentNumber(LocalDateTime startTime) {
        String date = startTime.toLocalDate().format(DateTimeFormatter.BASIC_ISO_DATE);
        String suffix = UUID.randomUUID().toString().replace("-", "").substring(0, 12).toUpperCase();
        return "APT%s%s".formatted(date, suffix);
    }
}
