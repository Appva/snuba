from contextlib import contextmanager
import logging
import redis
import simplejson as json
import time
import uuid

from snuba import settings

logger = logging.getLogger('snuba.state')

rds = redis.StrictRedis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB
)

# Window for concurrent query counting
max_query_duration_s = 60
# Window for determining query rate
rate_lookback_s = 60
# Amount of time we keep rate history
rate_history_s = 3600


@contextmanager
def rate_limit(bucket, per_second_limit=None, concurrent_limit=None):
    """
    A context manager for rate limiting that allows for limiting based on
    on a rolling-window per-second rate as well as the number of requests
    concurrently running.

    Uses a single redis sorted set per rate-limiting bucket to track both the
    concurrency and rate, the score is the query timestamp. Queries are thrown
    ahead in time when they start so we can count them as concurrent, and
    thrown back to their start time once they finish so we can count them
    towards the historical rate.

               time >>----->
    +-----------------------------+--------------------------------+
    | historical query window     | currently executing queries    |
    +-----------------------------+--------------------------------+
                                  ^
                                 now
    """
    bucket = 'snuba-ratelimit:{}'.format(bucket)
    query_id = uuid.uuid4()
    now = time.time()

    pipe = rds.pipeline(transaction=False)
    pipe.zremrangebyscore(bucket, '-inf', '({:f}'.format(now - rate_history_s))  # cleanup
    pipe.zadd(bucket, now + max_query_duration_s, query_id)  # add query
    pipe.zcount(bucket, now - rate_lookback_s, now)  # get rate
    pipe.zcount(bucket, '({:f}'.format(now), '+inf')  # get concurrent
    try:
        _, _, rate, concurrent = pipe.execute()
    except Exception as ex:
        logger.error(ex)
        yield True  # fail open if redis is having issues
        return

    per_second = rate / float(rate_lookback_s)
    allowed = (per_second_limit is None or per_second <= per_second_limit) and\
        (concurrent_limit is None or concurrent <= concurrent_limit)
    try:
        yield allowed
    finally:
        try:
            if allowed:
                # return the query to its start time
                rds.zincrby(bucket, query_id, -float(max_query_duration_s))
            else:
                rds.zrem(bucket, query_id)  # not allowed / not counted
        except Exception as ex:
            logger.error(ex)
            pass


def get_concurrent(bucket):
    now = time.time()
    bucket = 'snuba-ratelimit:{}'.format(bucket)
    return rds.zcount(bucket, '({:f}'.format(now), '+inf')


def get_rates(bucket, rollup=60):
    now = int(time.time())
    bucket = 'snuba-ratelimit:{}'.format(bucket)
    pipe = rds.pipeline(transaction=False)
    for i in reversed(range(now - rollup, now - rate_history_s, -rollup)):
        pipe.zcount(bucket, i, '({:f}'.format(i + rollup))
    return [c / float(rollup) for c in pipe.execute()]


def set_config(key, value):
    key = 'snuba_config:{}'.format(key)
    try:
        rds.set(key, value)
    except Exception as ex:
        logger.error(ex)
        pass


def get_config(key, default=None, numeric=True):
    key = 'snuba_config:{}'.format(key)
    try:
        result = rds.get(key)
        if result is not None:
            if numeric:
                try:
                    return int(result)
                except ValueError:
                    return default
            else:
                return result
    except Exception as ex:
        logger.error(ex)
        pass
    return default


def delete_config(key):
    key = 'snuba_config:{}'.format(key)
    try:
        rds.delete(key)
    except Exception as ex:
        logger.error(ex)
        pass


def record_query(data):
    max_queries = 200
    data = json.dumps(data, for_json=True)
    try:
        rds.pipeline(transaction=False)\
            .lpush('snuba_queries', data)\
            .ltrim('snuba_queries', 0, max_queries - 1)\
            .execute()
    except Exception as ex:
        logger.error(ex)
        pass


def get_queries():
    try:
        queries = []
        for q in rds.lrange('snuba_queries', 0, -1):
            try:
                queries.append(json.loads(q))
            except BaseException:
                pass
    except Exception as ex:
        logger.error(ex)

    return queries