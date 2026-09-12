"""Esquemas compartidos de Argus."""

from __future__ import annotations

from .incident import Incident, IncidentState, Severity, Signal, SignalKind, fingerprint

__version__ = "1.0.0"

__all__ = [
    "Incident",
    "IncidentState",
    "Severity",
    "Signal",
    "SignalKind",
    "fingerprint",
    "__version__",
]
