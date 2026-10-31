"""The admin API is reachable on 127.0.0.1 with the bearer token and NOT on any other interface of this machine."""

import asyncio
import socket

import httpx
import pytest
import uvicorn

from derivbot.api.app import create_app
from derivbot.killswitch.switch import KillSwitch, MemoryEventLog
from derivbot.kv.store import MemoryStore

TOKEN = "FAKE-ADMIN-TOKEN-ABCDEF123456"


def outbound_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))          # UDP connect sends nothing; it only selects the outbound interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


async def test_listens_on_loopback_only(tmp_path):
    ks = KillSwitch(MemoryStore(), MemoryEventLog(), tmp_path / "KILL")
    server = uvicorn.Server(uvicorn.Config(create_app(ks, TOKEN), host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started
    host, port = server.servers[0].sockets[0].getsockname()[:2]
    assert host == "127.0.0.1"
    try:
        async with httpx.AsyncClient() as c:
            assert (await c.get(f"http://127.0.0.1:{port}/health")).status_code == 200
            assert (await c.get(f"http://127.0.0.1:{port}/status")).status_code == 401
            ok = await c.get(f"http://127.0.0.1:{port}/status", headers={"Authorization": f"Bearer {TOKEN}"})
            assert ok.status_code == 200 and TOKEN not in ok.text
            ip = outbound_ip()
            if not ip or ip.startswith("127."):
                pytest.skip("no non-loopback address on this machine to test against")
            with pytest.raises(httpx.ConnectError):
                await c.get(f"http://{ip}:{port}/health", timeout=3)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
