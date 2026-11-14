"""Explicit state definitions for the footprint pipeline.

Lifecycle of a username search:

    engine rows (Raw)  ->  merge()  ->  apply_verification()  ->  score()
                           Stage.MERGED   Stage.VERIFIED          Stage.SCORED

Each transition function asserts the stage it expects, so calling them out of order raises StageError instead of
silently producing wrong confidence values.
"""
from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple


class Raw(StrEnum):
    """Per-engine verdict for one site."""
    CLAIMED = "Claimed"
    AVAILABLE = "Available"
    UNKNOWN = "Unknown"
    WAF = "WAF"
    ILLEGAL = "Illegal"

    @classmethod
    def parse(cls, value) -> "Raw":
        try:
            return cls(str(value))
        except ValueError:
            return cls.UNKNOWN

    @property
    def definitive(self) -> bool:
        return self in (Raw.CLAIMED, Raw.AVAILABLE)


class Found(StrEnum):
    """Merged verdict across engines."""
    FOUND = "Found"
    NOT_FOUND = "Not found"
    UNKNOWN = "Unknown"


class Verify(StrEnum):
    """Outcome of the second-request check on a found account."""
    NOT_RUN = "not run"        # verification switched off
    SKIPPED = "skipped"        # found, but beyond the verification cap
    OK = "ok"
    SUSPECT = "suspect"
    UNVERIFIED = "unverified"  # attempted, but blocked or unreachable


class Conf(StrEnum):
    NONE = ""
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    UNVERIFIED = "Unverified"


class Stage(StrEnum):
    MERGED = "merged"
    VERIFIED = "verified"
    SCORED = "scored"


CONF_ORDER = [Conf.HIGH.value, Conf.MEDIUM.value, Conf.LOW.value, Conf.UNVERIFIED.value]


class StageError(RuntimeError):
    """A pipeline step was called out of order."""


# ---- typed UI results (replace positional tuples in session_state) ----------------------------------------------
class UserResult(NamedTuple):
    recs: list
    ran: list
    diff: tuple | None      # (new_sites, gone_sites) vs the last run with identical settings
    had_prior: bool         # earlier runs exist (possibly with different settings)


class BrandResult(NamedTuple):
    handle: str
    recs: list
    sig: tuple


class EmailResult(NamedTuple):
    email: str
    basic: dict
    breaches: list
    breach_error: str
    holehe_sites: list
    had_api_key: bool
    holehe_used: bool


class DomainResult(NamedTuple):
    domain: str
    data: dict
