from devin.training.federated_council import (
    AXES,
    ArbiterRuntimeIdentity,
    CouncilAggregator,
    CouncilRouter,
    ReviewVerdict,
    ReviewerSpec,
    build_colibri_batch,
    manual_reviewer_roster,
    render_manual_review_bundle,
    resolve_arbiter_experiment,
)


def _roster(extra_family=False):
    base = [ReviewerSpec(f"review-{axis}", f"family-{axis}", (axis,)) for axis in AXES]
    if extra_family:
        base.extend(ReviewerSpec(f"second-{axis}", f"second-family-{axis}", (axis,)) for axis in AXES)
    return base


def _evidence():
    return {
        "attempt_id": "attempt-1",
        "prompt": "Implement the bounded feature",
        "response": "done",
        "evidence": [{"evidence_id": "sha256:" + "a" * 64}],
        "other_verdicts": ["must never leak"],
    }


def _verdict(packet, verdict="pass", family=None):
    return ReviewVerdict.from_mapping({
        "packet_id": packet["packet_id"],
        "axis": packet["axis"],
        "verdict": verdict,
        "confidence": 0.9,
        "reasoning": "The supplied evidence supports this axis.",
        "violations": [],
        "reviewer_id": packet["reviewer"]["reviewer_id"],
        "family": family or packet["reviewer"]["family"],
    })


def test_router_builds_blind_bounded_packets_with_full_coverage():
    plan = CouncilRouter().plan(_evidence(), _roster())
    assert plan["coverage_complete"] is True
    assert len(plan["packets"]) == 5
    assert all(packet["other_verdicts_included"] is False for packet in plan["packets"])
    assert all("other_verdicts" not in packet["evidence"] for packet in plan["packets"])


def test_critical_router_deduplicates_families_per_axis():
    plan = CouncilRouter().plan(_evidence(), _roster(extra_family=True), critical=True)
    for axis in AXES:
        families = [item["reviewer"]["family"] for item in plan["packets"] if item["axis"] == axis]
        assert len(families) == len(set(families))


def test_external_reviewer_requires_consent_and_approved_redaction():
    roster = _roster()
    roster[0] = ReviewerSpec("external-concept", "external-family", (AXES[0],), local=False)
    incomplete = CouncilRouter().plan(_evidence(), roster, external_consent=False)
    assert AXES[0] in incomplete["missing_axes"]
    try:
        CouncilRouter().plan(_evidence(), roster, external_consent=True)
    except ValueError as exc:
        assert "redacted packet" in str(exc)
    else:
        raise AssertionError("unredacted evidence admitted to an external reviewer")


def test_manual_bundle_covers_three_provider_families_without_sending():
    evidence = _evidence()
    evidence["external_packet"] = _evidence()
    evidence["redaction_manifest"] = {
        "approved": True,
        "automatic_send": False,
        "approved_by": "operator",
    }
    plan = CouncilRouter().plan(
        evidence,
        manual_reviewer_roster(),
        external_consent=True,
    )
    bundle = render_manual_review_bundle(plan)

    assert plan["coverage_complete"] is True
    assert bundle["automatic_send"] is False
    assert bundle["operator_copy_required"] is True
    assert {item["family"] for item in bundle["prompts"]} == {
        "openai", "anthropic", "google",
    }
    assert all("OUTPUT CONTRACT" in item["prompt"] for item in bundle["prompts"])
    assert all("hidden chain-of-thought" in item["prompt"] for item in bundle["prompts"])


def test_council_rejects_unbounded_evidence_before_building_prompts():
    evidence = _evidence()
    evidence["response"] = "x" * 40_000
    try:
        CouncilRouter().plan(evidence, _roster())
    except ValueError as exc:
        assert "bounded size" in str(exc)
    else:
        raise AssertionError("oversized council evidence admitted")


def test_aggregator_requires_coverage_and_never_promotes():
    plan = CouncilRouter().plan(_evidence(), _roster())
    partial = CouncilAggregator().aggregate(plan, [_verdict(plan["packets"][0])])
    assert partial["outcome"] == "needs_evidence"
    assert partial["promotion_performed"] is False

    complete = CouncilAggregator().aggregate(plan, [_verdict(packet) for packet in plan["packets"]])
    assert complete["outcome"] == "verified_success_candidate"
    assert complete["rerun_required"] is True
    assert complete["promotion_performed"] is False


def test_aggregator_rejects_reviewer_family_spoofing():
    plan = CouncilRouter().plan(_evidence(), _roster())
    forged = _verdict(plan["packets"][0], family="independent-looking-family")
    try:
        CouncilAggregator().aggregate(plan, [forged])
    except ValueError as exc:
        assert "family" in str(exc)
    else:
        raise AssertionError("forged reviewer family admitted")


def test_arbiter_uses_verified_experiment_not_model_opinion():
    pending = resolve_arbiter_experiment(
        axis="robustezza", experiment={"spec": "run the boundary test"},
        experiment_result={"status": "model_says_pass"},
    )
    assert pending["outcome"] == "needs_human_review"

    resolved = resolve_arbiter_experiment(
        axis="robustezza", experiment={"spec": "run the boundary test"},
        experiment_result={"status": "verified_pass", "evidence_id": "sha256:" + "b" * 64},
    )
    assert resolved["authority"] == "deterministic_experiment_result"
    assert resolved["promotion_performed"] is False


def test_colibri_batch_is_model_agnostic_and_stopped_by_default():
    plan = CouncilRouter().plan(_evidence(), _roster())
    result = CouncilAggregator().aggregate(
        plan, [_verdict(packet) for packet in plan["packets"]],
    )

    for model_id, family in (
        ("Qwen3.8-Flash-Next", "qwen"),
        ("Kimi-K3", "kimi"),
        ("GLM-5.3", "glm"),
    ):
        runtime = ArbiterRuntimeIdentity.from_mapping({
            "engine": "colibri",
            "model_id": model_id,
            "family": family,
            "revision": "sha256:" + "c" * 64,
        })
        batch = build_colibri_batch([result], runtime)
        assert batch["runtime"]["model_id"] == model_id
        assert batch["automatic_start"] is False
        assert batch["authority"] == "experiment_proposal_only"
        assert batch["held_out_policy"] == "golden_tasks_excluded_from_training_input"


def test_colibri_runtime_requires_immutable_model_revision():
    try:
        ArbiterRuntimeIdentity.from_mapping({
            "engine": "colibri",
            "model_id": "Qwen3.8-Flash-Next",
            "family": "qwen",
            "revision": "latest",
        })
    except ValueError as exc:
        assert "immutable sha256" in str(exc)
    else:
        raise AssertionError("mutable Colibri revision admitted")


def test_arbiter_provenance_does_not_replace_experiment_authority():
    runtime = ArbiterRuntimeIdentity.from_mapping({
        "engine": "colibri",
        "model_id": "Kimi-K3",
        "family": "kimi",
        "revision": "sha256:" + "d" * 64,
    })
    resolved = resolve_arbiter_experiment(
        axis="robustezza",
        experiment={"spec": "run the boundary test"},
        experiment_result={
            "status": "verified_pass",
            "evidence_id": "sha256:" + "b" * 64,
        },
        arbiter_runtime=runtime,
    )
    assert resolved["arbiter_runtime"]["model_id"] == "Kimi-K3"
    assert resolved["authority"] == "deterministic_experiment_result"
