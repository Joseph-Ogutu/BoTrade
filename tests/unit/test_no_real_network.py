import socket

import httpx
import pytest


def test_resolving_a_real_deriv_host_in_a_test_fails_loudly():
    with pytest.raises(OSError, match="real Deriv host"):
        socket.getaddrinfo("api.derivws.com", 443)
    with pytest.raises((OSError, httpx.HTTPError)) as exc:                 # httpx wraps the resolver error in its own ConnectError
        httpx.get("https://api.deriv.com/anything", timeout=2)
    assert "real Deriv host" in str(exc.value) or "real Deriv host" in repr(exc.value.__cause__)


def test_local_hosts_still_resolve():
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert socket.getaddrinfo("localhost", 80)
