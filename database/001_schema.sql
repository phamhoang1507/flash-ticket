USE flash_ticket;

SET time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS inventory (
    event_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    total INT UNSIGNED NOT NULL,
    remaining INT UNSIGNED NOT NULL,
    version BIGINT UNSIGNED NOT NULL DEFAULT 0,

    CONSTRAINT chk_inventory_bounds
        CHECK (remaining <= total)
) ENGINE = InnoDB;

CREATE TABLE IF NOT EXISTS bookings (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    event_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    quantity INT UNSIGNED NOT NULL,
    email VARCHAR(254) NOT NULL,

    idempotency_key VARCHAR(128) NOT NULL,
    request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,

    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    ticket_key VARCHAR(512) NULL,
    email_status VARCHAR(16) NOT NULL DEFAULT 'PENDING',

    lease_owner CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    lease_until DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    CONSTRAINT fk_booking_inventory
        FOREIGN KEY (event_id) REFERENCES inventory(event_id),

    CONSTRAINT uq_booking_idempotency
        UNIQUE (user_id, idempotency_key),

    CONSTRAINT chk_booking_quantity
        CHECK (quantity BETWEEN 1 AND 5),

    CONSTRAINT chk_booking_status
        CHECK (status IN ('PENDING', 'ISSUED')),

    CONSTRAINT chk_booking_email_status
        CHECK (email_status IN ('PENDING', 'SENT')),

    INDEX idx_booking_user (user_id)
) ENGINE = InnoDB
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_bin;

CREATE TABLE IF NOT EXISTS outbox (
    id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    aggregate_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    payload JSON NOT NULL,

    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    available_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    lease_owner CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    lease_until DATETIME(6) NULL,

    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    published_at DATETIME(6) NULL,

    CONSTRAINT fk_outbox_booking
        FOREIGN KEY (aggregate_id) REFERENCES bookings(id),

    CONSTRAINT uq_outbox_booking_event
        UNIQUE (aggregate_id, event_type),

    INDEX idx_outbox_pending (
        published_at,
        available_at,
        lease_until
    )
) ENGINE = InnoDB
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_bin;

INSERT INTO inventory (
    event_id,
    total,
    remaining,
    version
)
VALUES (
    '11111111-1111-4111-8111-111111111111',
    100,
    100,
    0
)
ON DUPLICATE KEY UPDATE event_id = inventory.event_id;