from packages.persistence.ids import uuid7


def test_uuid7_has_expected_version_and_variant() -> None:
    value = uuid7()

    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_uuid7_values_are_unique() -> None:
    values = {uuid7() for _ in range(1_000)}

    assert len(values) == 1_000
