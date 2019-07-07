from __future__ import annotations

import json

from .._typing import MYPY_CHECK_RUNNING

if MYPY_CHECK_RUNNING:
    from io import TextIOWrapper
    from typing import Dict

    from ..lib import ServerManager


class Commands:
    STATUS = "status"
    STOP = "stop"


def pretty_state(state: Dict) -> str:
    def pretty_object(o: Dict, indent=0):
        indent_text = "  " * indent
        lines = []
        for k in sorted(o.keys()):
            v = o[k]
            pretty_k = pretty_key(k)
            if isinstance(v, dict):
                lines.append(f"{indent_text}{pretty_k}:")
                lines.extend(pretty_object(v, indent + 1))
            else:
                lines.append(f"{indent_text}{pretty_k}: {v!r}")
        return lines

    def pretty_key(k: str):
        return k.replace("_", " ").title()

    return "\n".join(pretty_object(state))


class CliServerManager:
    def __init__(self, manager: ServerManager, output: TextIOWrapper):
        self._manager = manager
        self._output = output

    def stop(self) -> None:
        server_running = self._manager.server_running
        if server_running:
            self._writeln("Stopping...\n")
            self._manager.stop_server()
            self._writeln("Stopped.\n")
        else:
            self._writeln("Server already down.")

    def print_status(self, json_format: bool = False) -> None:
        server_running = self._manager.server_running

        state = {"status": "up" if server_running else "down"}

        if server_running:
            state.update(self._manager.server_state_raw)

        if json_format:
            self._writeln(json.dumps(state, sort_keys=True, separators=(",", ":")))
        else:
            self._writeln(pretty_state(state))

    def _writeln(self, text: str) -> None:
        self._output.writelines([text])
