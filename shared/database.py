import os

from sqlalchemy import create_engine


def create_db_engine(pool_size: int = 2):
    return create_engine(
        os.environ["DATABASE_URL"],
        isolation_level="READ COMMITTED",
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=3,
        pool_recycle=900,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 3,
            "read_timeout": 10,
            "write_timeout": 10,
            "init_command": (
                "SET time_zone = '+00:00', innodb_lock_wait_timeout = 3"
            ),
        },
    )