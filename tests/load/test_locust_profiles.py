"""Static contracts for the load profile registry."""

from __future__ import annotations

from tests.load.locustfile import PROFILE_CONFIGS, profile_options, selected_profile


def test_all_stage_one_profiles_are_registered() -> None:
    assert {
        "smoke",
        "load",
        "stress",
        "spike",
        "soak",
        "sse",
        "file-workflow",
        "saturation",
        "tenant-isolation",
    } <= PROFILE_CONFIGS.keys()


def test_profile_selection_is_explicit_and_seeded() -> None:
    profile = selected_profile({"POLICYFLOW_LOAD_PROFILE": "tenant-isolation"})
    assert profile.name == "tenant-isolation"
    assert (
        profile_options({"POLICYFLOW_LOAD_PROFILE": "smoke", "POLICYFLOW_LOAD_SEED": "7"})["seed"]
        == 7
    )


def test_profiles_use_route_workflows_not_health_only() -> None:
    assert (
        profile_options({"POLICYFLOW_LOAD_PROFILE": "sse"})["routes"]["sse"] == "/api/chat/stream"
    )
    assert profile_options({"POLICYFLOW_LOAD_PROFILE": "file-workflow"})["routes"][
        "upload"
    ].endswith("/documents")
    assert PROFILE_CONFIGS["tenant-isolation"].workflow == "isolation"
