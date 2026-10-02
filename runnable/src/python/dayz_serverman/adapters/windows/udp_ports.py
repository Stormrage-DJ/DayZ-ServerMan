"""Observe bound Windows UDP endpoints for restore port reservations."""

import subprocess


def bound_udp_ports() -> frozenset[int]:
    """Fail closed when the endpoint inventory cannot be read or understood."""
    result = subprocess.run(["netstat.exe", "-ano", "-p", "udp"], capture_output=True,
                            text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise OSError("UDP endpoint inventory is unavailable.")
    ports = set()
    for line in result.stdout.splitlines():
        columns = line.split()
        if columns and columns[0].upper() == "UDP":
            if len(columns) != 4:
                raise OSError("UDP endpoint inventory is incomplete.")
            port = int(columns[1].rsplit(":", 1)[1])
            if not 1 <= port <= 65535:
                raise OSError("UDP endpoint inventory is invalid.")
            ports.add(port)
    return frozenset(ports)
