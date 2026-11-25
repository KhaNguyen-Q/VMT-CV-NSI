"""Persistent references to successful statistical-analysis baselines."""

import json
import os

try:
    from analysis.comparator_config import load_comparator_config
    from analysis.treatment_profiles import canonical_treatment_profile
    from utils.utils import get_project_root
except ModuleNotFoundError:
    from app.analysis.comparator_config import load_comparator_config
    from app.analysis.treatment_profiles import canonical_treatment_profile
    from app.utils.utils import get_project_root


PROJECT_ROOT = get_project_root(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
REGISTRY_PATH = os.path.join(
    PROJECT_ROOT or os.getcwd(),
    "data",
    "json",
    "baseline_references.json",
)


def load_baseline_registry(path=None):
    path = path or REGISTRY_PATH
    if not os.path.isfile(path):
        return {"baseline_references": {}}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            registry = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read baseline registry: {exc}") from exc
    if not isinstance(registry.get("baseline_references"), dict):
        raise ValueError("Baseline registry is missing baseline_references.")
    return registry


def save_baseline_registry(registry, path=None):
    path = path or REGISTRY_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(registry, handle, indent=2)
        handle.write("\n")
    return path


def register_baseline(treatment_profile, config_path, run_name=None, set_default=False, path=None):
    """Register an available comparator config for one treatment profile."""
    profile = canonical_treatment_profile(treatment_profile)
    config_path = os.path.abspath(os.fspath(config_path))
    loaded = load_comparator_config(config_path)

    profile_config = loaded["treatment_profiles"]
    if profile not in profile_config and not any(
        canonical_treatment_profile(existing) == profile
        for existing in profile_config
    ):
        raise ValueError(
            f'Treatment profile "{profile}" is not present in the comparator configuration.'
        )

    registry = load_baseline_registry(path)
    references = registry["baseline_references"]
    profile_entry = references.setdefault(
        profile,
        {"default_config": None, "available_configs": []},
    )
    available = profile_entry.setdefault("available_configs", [])
    entry = {
        "name": run_name or os.path.basename(os.path.dirname(config_path)),
        "config_path": config_path,
    }
    available[:] = [
        existing
        for existing in available
        if os.path.normcase(os.path.abspath(existing.get("config_path", "")))
        != os.path.normcase(config_path)
    ]
    available.append(entry)
    if set_default:
        profile_entry["default_config"] = config_path
    save_baseline_registry(registry, path)
    return registry


def set_default_baseline(treatment_profile, config_path, path=None):
    profile = canonical_treatment_profile(treatment_profile)
    registry = load_baseline_registry(path)
    profile_entry = registry["baseline_references"].get(profile)
    if profile_entry is None:
        raise ValueError(f'No baseline references exist for "{profile}".')
    profile_entry["default_config"] = os.path.abspath(os.fspath(config_path))
    save_baseline_registry(registry, path)


def get_profile_baselines(treatment_profile, path=None):
    profile = canonical_treatment_profile(treatment_profile)
    registry = load_baseline_registry(path)
    entry = registry["baseline_references"].get(profile)
    if entry is None:
        return None, []
    available = [
        existing
        for existing in entry.get("available_configs", [])
        if os.path.isfile(existing.get("config_path", ""))
    ]
    default = entry.get("default_config")
    if default and not os.path.isfile(default):
        default = None
    return default, available
