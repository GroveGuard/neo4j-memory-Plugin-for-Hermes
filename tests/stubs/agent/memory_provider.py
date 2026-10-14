import threading, contextvars
class MemoryProvider: pass
def spawn_context_thread(fn, name=None):
    ctx = contextvars.copy_context()
    return threading.Thread(target=lambda: ctx.run(fn), name=name)
