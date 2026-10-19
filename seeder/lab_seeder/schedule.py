"""Runs the steps of a seed in the order their dependencies ask, and at the same time when they do not depend on one another.

    run(names, needs, work)             each name after every name it needs; work(name) is called once for each
    run(names, needs, work, back=True)  the other way round, for a removal: each name after every name that needs it

`names` is the order a seed used to run in, which is also the order of the run when SEED_WORKERS is 1, and `needs` maps a name
to the names it needs, all of which come before it in `names`. A step that fails ends the run: no other step starts, those
that run stop at their next request, and the failure of the first of them in order is raised once they have.

Standard library only."""

import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import common


def order_of(names, needs):
    """Why `needs` cannot be run in the order of `names`, or None: a name that needs one that is unknown or comes after it."""
    seen = []
    for name in names:
        for need in needs.get(name, ()):
            if need not in seen:
                return f"{name} needs {need}, which does not come before it"
        seen.append(name)
    return None


def run(names, needs, work, back=False):
    names = list(names)
    problem = order_of(names, needs)
    if problem:
        raise common.SeedError(problem)
    waits = {name: set(needs.get(name, ())) for name in names}
    if back:   # a step waits for those that needed it
        waits = {name: {other for other in names if name in needs.get(other, ())} for name in names}
    if common.WORKERS <= 1:
        for name in (reversed(names) if back else names):
            work(name)
        return
    done, running, started, failed = set(), {}, set(), {}

    def worked(name):
        work(name)
        common.milestone(name)   # whatever waits for the end of this step (common.reached) goes on
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=len(names) or 1) as pool:
        def launch():
            for name in (reversed(names) if back else names):
                if name not in started and waits[name] <= done and not common.STOP.is_set():
                    started.add(name)
                    running[pool.submit(common.guarded, worked, name)] = name

        with lock:
            launch()
        while running:
            finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
            with lock:
                for future in finished:
                    name = running.pop(future)
                    if future.exception() is None:
                        done.add(name)
                    else:
                        failed[name] = future.exception()
                launch()
    for name in names:   # the failure of the first step in order that failed, whichever was first in time
        if name in failed and not isinstance(failed[name], common.Stopped):
            raise failed[name]
    if common.STOP.is_set():
        raise common.failure() or common.SeedError("the seed was stopped")
