CREATE TABLE technician (
    id BIGINT NOT NULL AUTO_INCREMENT,
    name VARCHAR(64) NOT NULL,
    gender VARCHAR(16) NULL,
    strength VARCHAR(512) NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uk_technician_name UNIQUE (name)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci;

CREATE TABLE technician_schedule (
    id BIGINT NOT NULL AUTO_INCREMENT,
    technician_id BIGINT NOT NULL,
    start_time DATETIME(6) NOT NULL,
    end_time DATETIME(6) NOT NULL,
    status VARCHAR(16) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_schedule_technician
        FOREIGN KEY (technician_id) REFERENCES technician (id),
    CONSTRAINT chk_schedule_time CHECK (end_time > start_time),
    CONSTRAINT chk_schedule_status CHECK (status IN ('BUSY', 'FREE')),
    INDEX ix_schedule_technician_time (technician_id, start_time, end_time, status)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci;

INSERT INTO technician (name, gender, strength, enabled) VALUES
    ('张伟', '男', '擅长深层组织按摩，力气大，善于缓解肩颈腰背酸痛，注重肌肉深层放松', TRUE),
    ('王强', '男', '深层组织按摩专家，手法扎实，专注于运动损伤修复和肌肉放松', TRUE),
    ('李娜', '女', '手法细腻，擅长舒缓放松，适合压力大、睡眠差人群', TRUE),
    ('赵敏', '女', '精通经络推拿，善于调理亚健康，力气适中', TRUE),
    ('刘洋', '男', '泰式按摩高手，拉伸到位，适合喜欢全身放松的客户', TRUE),
    ('孙丽', '女', '芳香精油按摩，舒缓情绪，适合女性客户', TRUE),
    ('周杰', '男', '中医推拿，针对颈椎、腰椎问题有丰富经验', TRUE),
    ('吴婷', '女', '头部按摩和足疗专家，助眠效果好', TRUE),
    ('郑斌', '男', '力气大，适合喜欢重手法的客户，善于肌肉放松', TRUE),
    ('何静', '女', '淋巴引流、面部护理，适合美容养生需求', TRUE);
