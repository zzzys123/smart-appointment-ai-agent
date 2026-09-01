package com.smartappointment.appointment;

import com.smartappointment.appointment.technician.dto.TechnicianResponse;
import com.smartappointment.appointment.technician.entity.Technician;
import com.smartappointment.appointment.technician.repository.TechnicianRepository;
import com.smartappointment.appointment.technician.repository.TechnicianScheduleRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpStatus;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class AppointmentServiceApplicationTest {

    @LocalServerPort
    private int port;

    @Autowired
    private TestRestTemplate restTemplate;

    @Autowired
    private TechnicianRepository technicianRepository;

    @Autowired
    private TechnicianScheduleRepository scheduleRepository;

    @BeforeEach
    void setUp() {
        scheduleRepository.deleteAll();
        technicianRepository.deleteAll();
        technicianRepository.save(new Technician(
                "张伟",
                "男",
                "深层组织按摩",
                true
        ));
    }

    @Test
    void startsApplicationAndExposesReadApiHealthAndOpenApi() {
        var technicians = restTemplate.getForEntity(
                url("/internal/v1/technicians"),
                TechnicianResponse[].class
        );
        var health = restTemplate.getForEntity(url("/actuator/health"), String.class);
        var openApi = restTemplate.getForEntity(url("/v3/api-docs"), String.class);

        assertThat(technicians.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(technicians.getBody()).hasSize(1);
        assertThat(technicians.getBody()[0].name()).isEqualTo("张伟");

        assertThat(health.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(health.getBody()).contains("UP");

        assertThat(openApi.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(openApi.getBody())
                .contains("Smart Appointment Domain Service")
                .contains("/internal/v1/technicians/available");
    }

    private String url(String path) {
        return "http://127.0.0.1:%d%s".formatted(port, path);
    }
}
