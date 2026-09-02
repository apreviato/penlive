"""Plugin framework for the Tools section.

A plugin is the *user-facing* half of a privileged capability: a name, a
category, a parameter form for the UI, and a translation from submitted form
values into a daemon operation name plus structured arguments.

The split matters. Plugins live in the unprivileged API and can be added
freely; they cannot widen what the system is able to do, because the daemon
only accepts operation names it already knows (daemon/operations.py and
daemon/procedures.py). A plugin that asked for something outside that set
would simply be rejected.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Category = Literal["backup", "diagnostics", "repair", "recovery", "provisioning"]
Danger = Literal["safe", "caution", "destructive"]


@dataclass
class Param:
    """One field in the auto-generated parameter form."""
    name: str
    label: str
    type: Literal["device", "partition", "block_device", "text", "select", "backup_image", "checkbox"]
    required: bool = True
    default: Any = None
    help: str | None = None
    options: list[dict[str, str]] = field(default_factory=list)  # for type="select"
    # Only offer partitions whose filesystem is in this list (type="partition").
    fstypes: list[str] = field(default_factory=list)


@dataclass
class JobSpec:
    """What to ask the daemon for. `kind` must name a real daemon operation."""
    kind: str
    args: dict[str, Any]
    title: str


class Plugin(ABC):
    id: str
    name: str
    description: str
    category: Category
    danger: Danger = "safe"
    icon: str = "•"
    # Binaries that must exist in the live system for this plugin to work. The
    # UI greys the plugin out and names what's missing rather than letting the
    # user submit a form that can only fail.
    required_tools: tuple[str, ...] = ()
    params: tuple[Param, ...] = ()
    # Optional per-select requirements. The API removes choices whose exact
    # filesystem utility is absent instead of advertising a form that can only
    # fail after submission.
    option_requirements: dict[str, dict[str, tuple[str, ...]]] = {}

    @abstractmethod
    def build_job(self, values: dict[str, Any]) -> JobSpec:
        """Translate submitted form values into a daemon job request."""

    def manifest(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "danger": self.danger,
            "icon": self.icon,
            "required_tools": list(self.required_tools),
            "params": [asdict(p) for p in self.params],
        }


class PluginError(ValueError):
    """A plugin rejected the submitted values before anything privileged ran."""
