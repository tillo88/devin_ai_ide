from devin.ai.capability_router import assess_canary, load_routing_profile, plan_capability_route


def test_coding_is_dedicated_to_devin_without_automatic_switch():
    route = plan_capability_route("coding", resident_role="clippy")
    assert route["target_role"] == "devin"
    assert route["status"] == "activation_required"
    assert route["automatic_switch"] is False


def test_quick_question_reuses_compatible_resident_role():
    route = plan_capability_route("quick_question", resident_role="devin")
    assert route["target_role"] == "devin"
    assert route["status"] == "ready"
    assert route["reason"] == "compatible_resident_reuse"


def test_future_roles_are_declared_but_disabled():
    profile = load_routing_profile()
    assert profile.roles["hermes"]["enabled"] is False
    assert profile.roles["teacher"]["enabled"] is False
    assert plan_capability_route("image")["status"] == "unavailable"
    assert plan_capability_route("training")["status"] == "unavailable"


def test_canary_requires_distinct_verified_receipts_for_exact_profile():
    profile = load_routing_profile()
    good = {
        "receipt_id": "sha256:" + "a" * 64,
        "status": "verified_pass",
        "role": "devin",
        "capability": "coding",
        "profile_fingerprint": profile.fingerprint,
    }
    one = assess_canary([good], role="devin", capability="coding", profile=profile)
    assert one["canary_passed"] is False
    second = {**good, "receipt_id": "sha256:" + "b" * 64}
    complete = assess_canary([good, second], role="devin", capability="coding", profile=profile)
    assert complete["canary_passed"] is True
    assert complete["automatic_promotion"] is False


def test_canary_rejects_disabled_future_role():
    try:
        assess_canary([], role="teacher", capability="training")
    except ValueError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("disabled future role admitted to canary")
