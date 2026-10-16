import contextvars
import threading

SPAWNED = []          # names of threads started through spawn_context_thread (the only allowed way)


class MemoryProvider:
    pass


def spawn_context_thread(fn, name=None):
    ctx = contextvars.copy_context()
    SPAWNED.append(name)
    return threading.Thread(target=lambda: ctx.run(fn), name=name)
