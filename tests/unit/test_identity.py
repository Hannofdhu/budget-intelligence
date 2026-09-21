from packages.domain.identity.models import Actor, Role


def test_actor_role_membership_is_explicit() -> None:
    actor = Actor(
        user_id="user-1",
        department_id="department-1",
        roles=frozenset({Role.DATA_MAINTAINER}),
    )

    assert actor.has_role(Role.DATA_MAINTAINER)
    assert not actor.has_role(Role.DEPARTMENT_BUDGET_OWNER)
