from __future__ import annotations

import ipaddress
import os
import socket
import sys
import threading
import webbrowser

from waitress import create_server

from app import app


def _positive_port(raw_value: str) -> int:
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise SystemExit("PORT 必须是 1 到 65535 之间的整数。") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("PORT 必须是 1 到 65535 之间的整数。")
    return port


def _lan_addresses() -> list[str]:
    candidates: list[str] = []

    # Ask Windows which interface owns the default route. UDP connect() only
    # selects a route here; it does not send a packet.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 1))
        candidates.append(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass

    usable: list[str] = []
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version == 4 and not address.is_loopback and not address.is_link_local:
            usable.append(candidate)
    return list(dict.fromkeys(usable))


def main() -> None:
    if sys.version_info < (3, 10):
        raise SystemExit("需要 Python 3.10 或更高版本。")

    host = os.environ.get("HOST", "0.0.0.0")
    port = _positive_port(os.environ.get("PORT", "8000"))
    lan_addresses = _lan_addresses() if host in {"0.0.0.0", "::"} else [host]
    preferred_host = lan_addresses[0] if lan_addresses else "localhost"
    detected_url = f"http://{preferred_host}:{port}"
    preferred_url = os.environ.get("SHARE_BASE_URL", "").strip().rstrip("/") or detected_url
    app.config["LAN_BASE_URL"] = preferred_url

    # create_server binds the port before we announce success or open a browser.
    http_server = create_server(
        app,
        host=host,
        port=port,
        threads=8,
        max_request_body_size=int(app.config["MAX_CONTENT_LENGTH"]),
    )

    print()
    print("  像素对照台已启动")
    print("  ─────────────────────────────────")
    print(f"  本机访问： http://localhost:{port}")
    if lan_addresses:
        for index, address in enumerate(lan_addresses):
            label = "局域网地址" if index == 0 else "备用地址  "
            print(f"  {label}： http://{address}:{port}")
    else:
        print("  未自动找到局域网 IP；可用 ipconfig 查看本机 IPv4 地址。")
    print("  首次出现防火墙提示时，请允许在“专用网络”中通信。")
    print("  关闭此窗口即可停止服务。")
    print("  ─────────────────────────────────")
    print(flush=True)

    if os.environ.get("OPEN_BROWSER", "1").lower() not in {"0", "false", "no"}:
        threading.Timer(0.9, webbrowser.open, args=(preferred_url,)).start()

    try:
        http_server.run()
    finally:
        http_server.close()
        print("\n服务已停止。")


if __name__ == "__main__":
    main()
