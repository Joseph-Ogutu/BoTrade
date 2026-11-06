"""Suite-wide guard: no test may reach a real Deriv host (CLAUDE.md rule: never point tests at a real endpoint).

Found 2026-11-06: an app test called the dashboard summary with a fake token and the accounts call went out to the real API, then timed
out under load. Any attempt to resolve a hostname containing "deriv" now fails at once with a message that names the host. Local fake
servers (127.0.0.1, localhost) and everything else are untouched.
"""

import socket

import pytest


@pytest.fixture(autouse=True)
def _no_real_deriv_hosts(monkeypatch):
    real = socket.getaddrinfo

    def guard(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if isinstance(name, str) and "deriv" in name.lower():
            raise OSError(f"a test tried to reach a real Deriv host: {name}. Use the fake server or a stub.")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guard)
