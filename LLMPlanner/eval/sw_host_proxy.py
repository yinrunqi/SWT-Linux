"""Forward to the SolidWorks HTTP service with Host: localhost.

The C# listener rejects Host: 127.0.0.1. The SSH reverse tunnel is reached
as 127.0.0.1, so this proxy rewrites that header and closes the upstream
connection after each response.

SW_UPSTREAM_PORT is the Linux port of the tunnel (default 15110), which
forwards to the C# service on Windows port 5100.
"""
import os
import socket
import threading

LISTEN = ("127.0.0.1", int(os.environ.get("SW_PROXY_PORT", "15100")))
TARGET = ("127.0.0.1", int(os.environ.get("SW_UPSTREAM_PORT", "15110")))


def _rewrite(header: bytes) -> bytes:
    lines = header.split(b"\r\n")
    out = []
    saw_connection = False
    for index, line in enumerate(lines):
        if index > 0 and line.lower().startswith(b"host:"):
            out.append(b"Host: localhost")
        elif index > 0 and line.lower().startswith(b"connection:"):
            out.append(b"Connection: close")
            saw_connection = True
        else:
            out.append(line)
    if not saw_connection:
        out.append(b"Connection: close")
    return b"\r\n".join(out)


def _read_message(sock: socket.socket) -> tuple[bytes, bytes]:
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(1024 * 1024)
        if not chunk:
            return buf, b""
        buf += chunk
    head, body = buf.split(b"\r\n\r\n", 1)
    length = 0
    for line in head.split(b"\r\n")[1:]:
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
            break
    while len(body) < length:
        chunk = sock.recv(1024 * 1024)
        if not chunk:
            break
        body += chunk
    return head, body[:length]


def _handle(client: socket.socket) -> None:
    upstream = None
    try:
        client.settimeout(600)
        head, body = _read_message(client)
        if not head:
            return
        upstream = socket.create_connection(TARGET, timeout=600)
        upstream.settimeout(600)
        upstream.sendall(_rewrite(head) + b"\r\n\r\n" + body)
        while True:
            data = upstream.recv(1024 * 1024)
            if not data:
                break
            client.sendall(data)
    except Exception:
        try:
            client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
        except Exception:
            pass
    finally:
        for sock in (upstream, client):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass


def main() -> None:
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(LISTEN)
    server.listen(64)
    print(f"sw proxy {LISTEN[0]}:{LISTEN[1]} -> {TARGET[0]}:{TARGET[1]}", flush=True)
    while True:
        client, _ = server.accept()
        threading.Thread(target=_handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
