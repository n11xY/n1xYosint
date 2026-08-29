"""Bridge to the `holehe` project (MIT licensed, github.com/megadose/holehe)
for email-registration checks across 120+ sites, via each site's own
signup / password-reset flow signal ("this email is already registered" vs.
"no account found"). This never sends a real reset email or notifies the
target -- it only reads the site's own public response to that request,
exactly like holehe itself does.

Optional integration: only active if `holehe` and `httpx` are installed
(`pip install -e ".[holehe]"`, or `pip install holehe httpx`). If not
installed, this module is silently skipped like any unconfigured source --
no separate config needed.

We don't vendor holehe's ~120 site checks ourselves (they change often and
are already actively maintained upstream); instead this discovers and calls
holehe's own check functions directly, the same way holehe's own CLI does.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import pkgutil
from typing import Any, Callable, ClassVar, Optional

from osintrecon.core.logging_setup import get_logger
from osintrecon.core.models import Finding, Identifier, IdentifierType, MatchStatus
from osintrecon.plugins.base import SourcePlugin

log = get_logger("holehe_bridge")

_functions_cache: Optional[list[tuple[str, Callable]]] = None


def _discover_holehe_functions() -> list[tuple[str, Callable]]:
    """Mirrors holehe.core's own module discovery, so this stays in sync with
    whatever site modules the installed holehe version ships -- no hardcoded
    site list to maintain here."""
    global _functions_cache
    if _functions_cache is not None:
        return _functions_cache

    import holehe.modules as modules_pkg

    functions: list[tuple[str, Callable]] = []
    for _, mod_name, is_pkg in pkgutil.walk_packages(modules_pkg.__path__, modules_pkg.__name__ + "."):
        if is_pkg:
            continue
        try:
            module = importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001 - one broken holehe module shouldn't break the rest
            log.debug("failed to import holehe module %s: %s", mod_name, exc)
            continue
        site = mod_name.rsplit(".", 1)[-1]
        func = getattr(module, site, None)
        if callable(func):
            functions.append((site, func))

    _functions_cache = functions
    return functions


async def _run_one(func: Callable, email: str, client: Any, out: list[dict]) -> None:
    try:
        await func(email, client, out)
    except Exception as exc:  # noqa: BLE001 - one broken holehe module shouldn't kill the batch
        log.debug("holehe module %s raised: %s", getattr(func, "__name__", "?"), exc)


def _summarize_extras(result: dict) -> str:
    """The password-reset/signup flow this technique relies on almost never
    reveals a username -- sites don't leak account details through it, only
    a yes/no existence signal. A few holehe modules do capture incidental
    extras (a partial phone number, a recovery email, sometimes a display
    name); surface those in the title when present so they're visible
    without digging into the exported metadata."""
    parts = []
    if result.get("phoneNumber"):
        parts.append(f"phone: {result['phoneNumber']}")
    if result.get("emailrecovery"):
        parts.append(f"recovery: {result['emailrecovery']}")
    others = result.get("others")
    if isinstance(others, dict):
        for key in ("username", "name", "fullname", "FullName", "displayName"):
            if others.get(key):
                parts.append(f"{key}: {others[key]}")
                break
    return ", ".join(parts)


class HoleheBridgePlugin(SourcePlugin):
    name: ClassVar[str] = "holehe"
    category: ClassVar[str] = "social"
    accepts: ClassVar[set[IdentifierType]] = {IdentifierType.EMAIL}
    requires_api_key: ClassVar[bool] = True  # "configured" here means "installed", not a key
    description: ClassVar[str] = (
        "Checks 120+ sites' signup/password-reset flows for email registration "
        "(optional: pip install -e \".[holehe]\")."
    )

    def is_configured(self) -> bool:
        return (
            importlib.util.find_spec("holehe") is not None
            and importlib.util.find_spec("httpx") is not None
        )

    async def run(self, identifier: Identifier) -> list[Finding]:
        import httpx

        try:
            functions = _discover_holehe_functions()
        except Exception as exc:  # noqa: BLE001
            log.error("holehe module discovery failed: %s", exc)
            return [Finding(
                source=self.name, identifier=identifier, status=MatchStatus.ERROR,
                source_url="", title="holehe module discovery failed", category=self.category,
                metadata={"error": str(exc)},
            )]

        out: list[dict] = []
        async with httpx.AsyncClient(timeout=10) as client:
            await asyncio.gather(
                *(_run_one(func, identifier.value, client, out) for _, func in functions),
                return_exceptions=True,
            )

        findings = []
        for result in out:
            if not result.get("exists"):
                continue
            site_name = result.get("name") or result.get("domain", "unknown")
            domain = result.get("domain", "")
            extra_hint = _summarize_extras(result)
            title = f"Email registered on {site_name}" + (f" ({extra_hint})" if extra_hint else "")
            findings.append(Finding(
                source=f"{self.name}:{site_name}",
                identifier=identifier,
                status=MatchStatus.CONFIRMED,
                source_url=f"https://{domain}" if domain else "",
                title=title,
                category=self.category,
                metadata={
                    "site": site_name,
                    "domain": domain,
                    "email_recovery": result.get("emailrecovery"),
                    "phone_number": result.get("phoneNumber"),
                    "others": result.get("others"),
                    "rate_limited": result.get("rateLimit"),
                },
            ))
        return findings
