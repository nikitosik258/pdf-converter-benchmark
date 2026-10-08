from __future__ import annotations

import importlib.metadata


def installed_versions(distributions: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in distributions:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return out
