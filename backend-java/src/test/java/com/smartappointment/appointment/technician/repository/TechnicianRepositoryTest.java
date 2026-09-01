package com.smartappointment.appointment.technician.repository;

import com.smartappointment.appointment.booking.entity.AppointmentStatus;
import com.smartappointment.appointment.technician.entity.ScheduleStatus;
import com.smartappointment.appointment.technician.entity.Technician;
import com.smartappointment.appointment.technician.entity.TechnicianSchedule;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
class TechnicianRepositoryTest {

    @Autowired
    private TechnicianRepository technicianRepository;

    @Autowired
    private TechnicianScheduleRepository scheduleRepository;

    @Test
    void excludesOnlyEnabledTechniciansWithOverlappingBusySchedules() {
        Technician busy = technicianRepository.save(
                new Technician("张伟", "男", "深层组织按摩", true)
        );
        Technician free = technicianRepository.save(
                new Technician("李娜", "女", "舒缓放松", true)
        );
        Technician disabled = technicianRepository.save(
                new Technician("停用技师", "女", "舒缓放松", false)
        );

        scheduleRepository.save(new TechnicianSchedule(
                busy,
                LocalDateTime.of(2026, 8, 24, 14, 0),
                LocalDateTime.of(2026, 8, 24, 15, 0),
                ScheduleStatus.BUSY
        ));
        scheduleRepository.save(new TechnicianSchedule(
                free,
                LocalDateTime.of(2026, 8, 24, 14, 0),
                LocalDateTime.of(2026, 8, 24, 15, 0),
                ScheduleStatus.FREE
        ));

        var available = technicianRepository.findAvailableTechnicians(
                LocalDateTime.of(2026, 8, 24, 14, 30),
                LocalDateTime.of(2026, 8, 24, 15, 30),
                null,
                null,
                ScheduleStatus.BUSY,
                AppointmentStatus.CONFIRMED
        );

        assertThat(available).extracting(Technician::getName).containsExactly("李娜");
        assertThat(available).doesNotContain(disabled);
    }

    @Test
    void treatsAdjacentSchedulesAsAvailableAndAppliesOptionalFilters() {
        Technician deepTissue = technicianRepository.save(
                new Technician("张伟", "男", "深层组织按摩", true)
        );
        technicianRepository.save(new Technician("王强", "男", "运动康复", true));
        technicianRepository.save(new Technician("李娜", "女", "深层组织按摩", true));

        scheduleRepository.save(new TechnicianSchedule(
                deepTissue,
                LocalDateTime.of(2026, 8, 24, 14, 0),
                LocalDateTime.of(2026, 8, 24, 15, 0),
                ScheduleStatus.BUSY
        ));

        var available = technicianRepository.findAvailableTechnicians(
                LocalDateTime.of(2026, 8, 24, 15, 0),
                LocalDateTime.of(2026, 8, 24, 16, 0),
                "男",
                "深层",
                ScheduleStatus.BUSY,
                AppointmentStatus.CONFIRMED
        );

        assertThat(available).extracting(Technician::getName).containsExactly("张伟");
    }
}
