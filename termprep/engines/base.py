"""Engine registry — auto-discovery and instantiation of translation engines."""

from __future__ import annotations

from typing import Type

from termprep.interfaces.translator import Translator

_registry: dict[str, Type[Translator]] = {}


def register_engine(cls: Type[Translator]) -> Type[Translator]:
    """Decorator to register a translator engine."""
    _registry[cls.name] = cls
    return cls


def get_engine(name: str) -> Type[Translator] | None:
    """Get an engine class by name."""
    return _registry.get(name)


def list_engines() -> list[str]:
    """Return all registered engine names."""
    return list(_registry.keys())


def build_available_engines(settings: dict | None = None) -> list[Translator]:
    """Instantiate all engines that are currently available.

    Args:
        settings: Optional configuration dict forwarded to each engine.

    Returns:
        List of available Translator instances, sorted by priority.
    """
    instances: list[Translator] = []
    for name, cls in _registry.items():
        try:
            instance = cls(settings=settings) if settings else cls()
            if instance.available:
                instances.append(instance)
        except Exception:
            continue
    instances.sort(key=lambda e: e.priority)
    return instances
