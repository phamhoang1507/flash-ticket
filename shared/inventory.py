import os

from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

PROJECT_INVENTORY = """
local current = redis.call('HGET', KEYS[1], 'version')
local incoming = ARGV[2]

if not current
   or #incoming > #current
   or (#incoming == #current and incoming > current)
then
    redis.call(
        'HSET', KEYS[1],
        'remaining', ARGV[1],
        'version', incoming
    )
    return 1
end

return 0
"""


def create_redis_client() -> Redis:
    return Redis.from_url(
        os.environ["REDIS_URL"],
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
        retry=Retry(NoBackoff(), 0),
    )


def project_inventory(
    client: Redis,
    event_id: str,
    remaining: int,
    version: int,
) -> bool:
    if type(remaining) is not int or type(version) is not int:
        raise ValueError("Inventory values must be integers")

    if remaining < 0 or version < 0:
        raise ValueError("Inventory values must be nonnegative")

    return bool(
        client.eval(
            PROJECT_INVENTORY,
            1,
            f"inventory:{event_id}",
            str(remaining),
            str(version),
        )
    )