import os
from redis import Redis
from rq import Worker, Queue

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
QUEUES = ["copilot"]


if __name__ == "__main__":
    redis_conn = Redis.from_url(REDIS_URL)
    queues = [Queue(q, connection=redis_conn) for q in QUEUES]
    worker = Worker(queues=queues, connection=redis_conn)
    worker.work(with_scheduler=True)
