package com.smartappointment.appointment.booking.integration;

import com.smartappointment.appointment.booking.dto.CreateAppointmentRequest;
import com.smartappointment.appointment.booking.exception.AppointmentSlotConflictException;
import com.smartappointment.appointment.booking.repository.AppointmentRepository;
import com.smartappointment.appointment.booking.repository.AppointmentSlotRepository;
import com.smartappointment.appointment.booking.service.AppointmentCommandService;
import com.smartappointment.appointment.technician.entity.Technician;
import com.smartappointment.appointment.technician.repository.TechnicianRepository;
import com.smartappointment.appointment.technician.repository.TechnicianScheduleRepository;
import com.smartappointment.appointment.technician.service.TechnicianQueryService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.MySQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.time.LocalDateTime;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
@Testcontainers(disabledWithoutDocker = true)
class AppointmentConcurrencyMySqlTest {

    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.4")
            .withDatabaseName("appointment_test")
            .withUsername("appointment")
            .withPassword("appointment_test");

    @DynamicPropertySource
    static void mysqlProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", MYSQL::getJdbcUrl);
        registry.add("spring.datasource.username", MYSQL::getUsername);
        registry.add("spring.datasource.password", MYSQL::getPassword);
        registry.add("spring.datasource.driver-class-name", () -> "com.mysql.cj.jdbc.Driver");
        registry.add("spring.jpa.hibernate.ddl-auto", () -> "validate");
        registry.add("spring.flyway.enabled", () -> "true");
    }

    @Autowired
    private AppointmentCommandService appointmentCommandService;

    @Autowired
    private AppointmentSlotRepository appointmentSlotRepository;

    @Autowired
    private AppointmentRepository appointmentRepository;

    @Autowired
    private TechnicianScheduleRepository technicianScheduleRepository;

    @Autowired
    private TechnicianRepository technicianRepository;

    @Autowired
    private TechnicianQueryService technicianQueryService;

    private long technicianId;
    private LocalDateTime startTime;

    @BeforeEach
    void setUp() {
        appointmentSlotRepository.deleteAll();
        appointmentRepository.deleteAll();
        technicianScheduleRepository.deleteAll();
        technicianRepository.deleteAll();
        technicianId = technicianRepository.save(new Technician(
                "并发测试技师",
                "女",
                "肩颈按摩",
                true
        )).getId();
        startTime = LocalDateTime.now()
                .plusDays(2)
                .withHour(10)
                .withMinute(0)
                .withSecond(0)
                .withNano(0);
    }

    @Test
    void concurrentRequestsForSameTechnicianAndTimeAllowOnlyOneSuccess() throws Exception {
        var executor = Executors.newFixedThreadPool(2);
        var ready = new CountDownLatch(2);
        var start = new CountDownLatch(1);

        Callable<String> first = contender("user-1", "session-1", "idempotency-1", ready, start);
        Callable<String> second = contender("user-2", "session-2", "idempotency-2", ready, start);

        try {
            var firstResult = executor.submit(first);
            var secondResult = executor.submit(second);
            assertThat(ready.await(10, TimeUnit.SECONDS)).isTrue();
            start.countDown();

            assertThat(List.of(firstResult.get(20, TimeUnit.SECONDS), secondResult.get(20, TimeUnit.SECONDS)))
                    .containsExactlyInAnyOrder("CREATED", "CONFLICT");
        } finally {
            executor.shutdownNow();
        }

        assertThat(appointmentRepository.count()).isEqualTo(1);
        assertThat(appointmentSlotRepository.count()).isEqualTo(2);
    }

    @Test
    void repeatedIdempotencyKeyReturnsOriginalAppointmentWithoutDuplicateRows() {
        CreateAppointmentRequest request = request("user-1", "session-1", startTime);

        var first = appointmentCommandService.createAppointment(request, "same-key");
        var replay = appointmentCommandService.createAppointment(request, "same-key");

        assertThat(first.created()).isTrue();
        assertThat(replay.created()).isFalse();
        assertThat(replay.response().appointmentNo()).isEqualTo(first.response().appointmentNo());
        assertThat(appointmentRepository.count()).isEqualTo(1);
        assertThat(appointmentSlotRepository.count()).isEqualTo(2);
        assertThat(technicianQueryService.findAvailableTechnicians(
                startTime,
                60,
                null,
                null
        )).isEmpty();
    }

    @Test
    void concurrentRequestsWithSameIdempotencyKeyReturnOneAppointment() throws Exception {
        var executor = Executors.newFixedThreadPool(2);
        var ready = new CountDownLatch(2);
        var start = new CountDownLatch(1);

        Callable<String> request = () -> {
            ready.countDown();
            if (!start.await(10, TimeUnit.SECONDS)) {
                throw new IllegalStateException("Concurrent start latch timed out");
            }
            return appointmentCommandService.createAppointment(
                    request("same-user", "same-session", startTime),
                    "concurrent-same-key"
            ).response().appointmentNo();
        };

        try {
            var first = executor.submit(request);
            var second = executor.submit(request);
            assertThat(ready.await(10, TimeUnit.SECONDS)).isTrue();
            start.countDown();

            assertThat(first.get(20, TimeUnit.SECONDS))
                    .isEqualTo(second.get(20, TimeUnit.SECONDS));
        } finally {
            executor.shutdownNow();
        }

        assertThat(appointmentRepository.count()).isEqualTo(1);
        assertThat(appointmentSlotRepository.count()).isEqualTo(2);
    }

    @Test
    void adjacentAppointmentsDoNotConflict() {
        var first = appointmentCommandService.createAppointment(
                request("user-1", "session-1", startTime),
                "adjacent-1"
        );
        var second = appointmentCommandService.createAppointment(
                request("user-2", "session-2", startTime.plusHours(1)),
                "adjacent-2"
        );

        assertThat(first.created()).isTrue();
        assertThat(second.created()).isTrue();
        assertThat(appointmentRepository.count()).isEqualTo(2);
        assertThat(appointmentSlotRepository.count()).isEqualTo(4);
    }

    private Callable<String> contender(
            String userId,
            String sessionId,
            String idempotencyKey,
            CountDownLatch ready,
            CountDownLatch start
    ) {
        return () -> {
            ready.countDown();
            if (!start.await(10, TimeUnit.SECONDS)) {
                throw new IllegalStateException("Concurrent start latch timed out");
            }
            try {
                appointmentCommandService.createAppointment(
                        request(userId, sessionId, startTime),
                        idempotencyKey
                );
                return "CREATED";
            } catch (AppointmentSlotConflictException exception) {
                return "CONFLICT";
            }
        };
    }

    private CreateAppointmentRequest request(
            String userId,
            String sessionId,
            LocalDateTime requestedStartTime
    ) {
        return new CreateAppointmentRequest(
                userId,
                sessionId,
                technicianId,
                "肩颈按摩",
                requestedStartTime,
                60
        );
    }
}
