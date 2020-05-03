# from isyntax2raw import *
from threading import BoundedSemaphore


class MaxQueuePool(object):
    """This Class wraps a concurrent.futures.Executor
    limiting the size of its task queue.
    If `max_queue_size` tasks are submitted, the next call to submit will
    block until a previously submitted one is completed.

    Brought in from:
      * https://gist.github.com/noxdafox/4150eff0059ea43f6adbdd66e5d5e87e

    See also:
      * https://www.bettercodebytes.com/
            theadpoolexecutor-with-a-bounded-queue-in-python/
      * https://pypi.org/project/bounded-pool-executor/
      * https://bugs.python.org/issue14119
      * https://bugs.python.org/issue29595
      * https://github.com/python/cpython/pull/143
    """
    def __init__(self, executor, max_queue_size, max_workers=None):
        if max_workers is None:
            max_workers = max_queue_size
        self.pool = executor(max_workers=max_workers)
        self.pool_queue = BoundedSemaphore(max_queue_size)

    def submit(self, function, *args, **kwargs):
        """Submits a new task to the pool, blocks if Pool queue is full."""
        self.pool_queue.acquire()

        future = self.pool.submit(function, *args, **kwargs)
        future.add_done_callback(self.pool_queue_callback)

        return future

    def pool_queue_callback(self, _):
        """Called once task is done, releases one queue slot."""
        self.pool_queue.release()

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        self.pool.__exit__(exception_type, exception_value, traceback)
