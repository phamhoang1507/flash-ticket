from sqlalchemy import text

from shared.database import create_db_engine
from shared.inventory import create_redis_client, project_inventory


def main():
    engine = create_db_engine()
    redis = create_redis_client()
    last_id = ""
    count = 0

    try:
        while True:
            with engine.connect() as connection:
                rows = connection.execute(
                    text("""
                        SELECT event_id, remaining, version
                        FROM inventory
                        WHERE event_id > :last_id
                        ORDER BY event_id
                        LIMIT 100
                    """),
                    {"last_id": last_id},
                ).mappings().all()

            if not rows:
                break

            for row in rows:
                project_inventory(
                    redis,
                    row["event_id"],
                    row["remaining"],
                    row["version"],
                )
                count += 1

            last_id = rows[-1]["event_id"]

        print(f"Reconciled {count} inventory rows")
    finally:
        redis.close()
        engine.dispose()


if __name__ == "__main__":
    main()