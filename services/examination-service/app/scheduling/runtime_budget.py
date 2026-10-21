"""Cooperative monotonic deadline for bounded, synchronous scheduling checks.

Context-local: other requests and ordinary validation have no deadline. Checks
in the existing validators/scorer interrupt expensive loops, rather than leaving
background work running after the repair caller returns.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

clock = perf_counter
_deadline = ContextVar("scheduling_deadline", default=None)


class SchedulingBudgetExpired(TimeoutError):
    pass


def check_budget():
    deadline = _deadline.get()
    if deadline is not None and clock() >= deadline:
        raise SchedulingBudgetExpired


@contextmanager
def scheduling_deadline(deadline):
    outer = _deadline.get()
    token = _deadline.set(deadline if outer is None else min(deadline, outer))
    try:
        check_budget()
        yield
    finally:
        _deadline.reset(token)
