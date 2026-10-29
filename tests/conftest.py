"""Shared pytest setup: each test starts with empty sign-in limits."""

import pytest

from stellis import rate_limit


@pytest.fixture(autouse=True)
def empty_rate_limits():
    """Clears every sign-in and sign-up window before each test."""
    for window in (rate_limit.LOGIN_FAILURES_BY_USERNAME,
                   rate_limit.LOGIN_FAILURES_BY_CLIENT,
                   rate_limit.SIGNUPS_BY_CLIENT):
        window.reset()
    yield
