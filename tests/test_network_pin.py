import socket


def test_ipv4_pin_applies_when_flag_set(monkeypatch):
    import urllib3.util.connection as conn
    import tradebot
    monkeypatch.setenv("TRADEBOT_FORCE_IPV4", "1")
    orig_gai, orig_has = socket.getaddrinfo, conn.HAS_IPV6
    try:
        if hasattr(socket, "_tradebot_v4_pinned"):
            delattr(socket, "_tradebot_v4_pinned")
        tradebot._apply_network_pins()
        assert conn.HAS_IPV6 is False
        fams = {ai[0] for ai in socket.getaddrinfo("localhost", 80)}
        assert fams == {socket.AF_INET}
    finally:
        socket.getaddrinfo, conn.HAS_IPV6 = orig_gai, orig_has
        if hasattr(socket, "_tradebot_v4_pinned"):
            delattr(socket, "_tradebot_v4_pinned")
