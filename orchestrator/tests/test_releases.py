"""The version and ticket rules of releases (task 10.1)."""

from orchestrator.releases import next_version, tickets


def test_the_next_version_is_the_next_minor_one_and_the_first_is_0_1_0():
    assert next_version([]) == "0.1.0"
    assert next_version(["latest", "v1", "0.3.0"]) == "0.1.0", "only v<major>.<minor>.<patch> tags count"
    assert next_version(["v0.1.0", "v0.2.0"]) == "0.3.0"
    assert next_version(["v0.9.0", "v0.10.0"]) == "0.11.0", "compared as numbers"
    assert next_version(["v0.4.0", "v1.0.0", "v1.2.3"]) == "1.3.0", "a major version set by hand is kept"


def test_the_tickets_are_the_projects_feature_merges_in_number_order():
    messages = [
        "Merge pull request 'EWA-12: x' (#9) from feat/EWA-12_x into development\n\nbody",
        "feat(EWA-12): x",
        "Merge pull request 'EWA-9: y' (#7) from feat/EWA-9_y into development",
        "Merge pull request 'EWA-9: y' (#8) from archive/EWA-9 into development",
        "Merge pull request 'OTHER-1: z' (#5) from feat/OTHER-1_z into development",
        "Merge pull request 'EWA-12: x' (#10) from feat/EWA-12_x into development",
    ]
    assert tickets(messages, "EWA") == ["EWA-9", "EWA-12"]
