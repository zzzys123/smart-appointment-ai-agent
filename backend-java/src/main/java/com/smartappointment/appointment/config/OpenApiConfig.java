package com.smartappointment.appointment.config;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class OpenApiConfig {

    @Bean
    OpenAPI appointmentServiceOpenApi() {
        return new OpenAPI().info(new Info()
                .title("Smart Appointment Domain Service")
                .description("Deterministic technician availability and appointment APIs for the Python AI agent")
                .version("v1"));
    }
}
