"""Each call's relay settings: the ICE servers the page connects through.

On LKE the bot's pod is reachable only through the in-cluster TURN relay, STUNner, so the page
gets the relay alone, over UDP and over TLS on 443. Locally it gets none and reaches the bot directly.
"""

import uuid

import httpx

from companion import settings


async def ice_servers() -> list[dict]:
    if not (settings.TURN_AUTH_URL and settings.TURN_TLS_HOST):
        return []  # local mode

    # A short-lived credential from STUNner's auth service, minted per call. Its UDP URL names a
    # node's current address, so it follows a node recycle:
    # https://github.com/l7mp/stunner/blob/v1.2.1/docs/AUTH.md
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{settings.TURN_AUTH_URL}/ice",
            params={"service": "turn", "username": uuid.uuid4().hex},
        )
    response.raise_for_status()
    servers = response.json()["iceServers"]

    # Keep only the UDP URLs: the TCP one is the TURN-TCP listener's in-cluster address, which a
    # browser cannot reach. TURN over TLS reaches that listener through the front on 443 instead,
    # by a hostname the auth service cannot know. Both listeners share one GatewayConfig, so one
    # credential opens either.
    udp = [url for server in servers for url in server["urls"] if url.endswith("?transport=udp")]
    tls = f"turns:{settings.TURN_TLS_HOST}:443?transport=tcp"
    return [
        {
            "urls": [*udp, tls],
            "username": servers[0]["username"],
            "credential": servers[0]["credential"],
        }
    ]
