"""Canonical treatment profile names used by range CSV exports and analysis."""

import re


TREATMENT_PROFILES = (
    "C2-(Left, Middle, Right)",
    "T1-(Left, Middle, Right)",
    "LUQ-RUQ",
)


def _profile_key(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold())


_CANONICAL_PROFILES = {
    _profile_key(profile): profile for profile in TREATMENT_PROFILES
}


def canonical_treatment_profile(value):
    """Normalize known profile spelling variants; preserve custom profile names."""
    profile = (value or "").strip()
    return _CANONICAL_PROFILES.get(_profile_key(profile), profile)
