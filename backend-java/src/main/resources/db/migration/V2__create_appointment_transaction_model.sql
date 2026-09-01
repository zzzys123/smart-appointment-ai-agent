CREATE TABLE appointment (
    id BIGINT NOT NULL AUTO_INCREMENT,
    appointment_no VARCHAR(40) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    technician_id BIGINT NOT NULL,
    service_name VARCHAR(128) NOT NULL,
    start_time DATETIME(6) NOT NULL,
    end_time DATETIME(6) NOT NULL,
    status VARCHAR(16) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uk_appointment_no UNIQUE (appointment_no),
    CONSTRAINT uk_appointment_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT fk_appointment_technician
        FOREIGN KEY (technician_id) REFERENCES technician (id),
    CONSTRAINT chk_appointment_time CHECK (end_time > start_time),
    CONSTRAINT chk_appointment_status CHECK (status IN ('CONFIRMED', 'CANCELED', 'COMPLETED')),
    INDEX ix_appointment_technician_time (technician_id, start_time, end_time, status)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci;

CREATE TABLE appointment_slot (
    id BIGINT NOT NULL AUTO_INCREMENT,
    appointment_id BIGINT NOT NULL,
    technician_id BIGINT NOT NULL,
    slot_start DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uk_technician_slot UNIQUE (technician_id, slot_start),
    CONSTRAINT fk_slot_appointment
        FOREIGN KEY (appointment_id) REFERENCES appointment (id),
    CONSTRAINT fk_slot_technician
        FOREIGN KEY (technician_id) REFERENCES technician (id),
    INDEX ix_slot_appointment (appointment_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci;
