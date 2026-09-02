from __future__ import annotations

from typing import Any

from .base import JobSpec, Plugin


class NetworkDiagnosticsPlugin(Plugin):
    id = "network-diagnostics"
    name = "Network Diagnostics"
    description = (
        "Check NetworkManager, addresses, routes, DNS and internet reachability. "
        "Read-only and useful when Wi-Fi is connected but downloads cannot reach a mirror."
    )
    category = "diagnostics"
    danger = "safe"
    icon = "NET"
    required_tools = ("nmcli", "ip", "getent", "ping", "curl")

    def build_job(self, values: dict[str, Any]) -> JobSpec:
        return JobSpec("network_diagnostics", {}, "Network diagnostics")
