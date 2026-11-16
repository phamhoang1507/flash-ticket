USE flash_ticket;

SET time_zone = '+00:00';

SET @event_id = '11111111-1111-4111-8111-111111111111';
SET @booking_id = UUID();
SET @outbox_id = UUID();

START TRANSACTION;

UPDATE inventory
SET remaining = remaining - 1,
    version = version + 1
WHERE event_id = @event_id
  AND remaining >= 1;

SELECT ROW_COUNT() AS inventory_updated;

SELECT remaining, version
INTO @remaining, @version
FROM inventory
WHERE event_id = @event_id;

INSERT INTO bookings (
    id,
    user_id,
    event_id,
    quantity,
    email,
    idempotency_key,
    request_hash
)
VALUES (
    @booking_id,
    'local-user',
    @event_id,
    1,
    'buyer@example.com',
    'schema-smoke-test',
    SHA2('schema-smoke-test', 256)
);

INSERT INTO outbox (
    id,
    aggregate_id,
    event_type,
    payload
)
VALUES (
    @outbox_id,
    @booking_id,
    'BookingCreated',
    JSON_OBJECT(
        'booking_id', @booking_id,
        'event_id', @event_id,
        'remaining', @remaining,
        'version', @version
    )
);

SELECT remaining, version
FROM inventory
WHERE event_id = @event_id;

SELECT id, status
FROM bookings
WHERE id = @booking_id;

SELECT event_type, payload
FROM outbox
WHERE id = @outbox_id;

ROLLBACK;

SELECT remaining, version
FROM inventory
WHERE event_id = @event_id;

SELECT COUNT(*) AS booking_after_rollback
FROM bookings
WHERE id = @booking_id;

SELECT COUNT(*) AS outbox_after_rollback
FROM outbox
WHERE id = @outbox_id;