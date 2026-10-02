"""P6 Federated Council planning and aggregation API (no model execution)."""

from fastapi import APIRouter, Request

from devin.training.federated_council import (
    AXES,
    ArbiterRuntimeIdentity,
    CapacityBudgeter,
    CouncilAggregator,
    CouncilRouter,
    ReviewVerdict,
    ReviewerSpec,
    build_manual_evidence_preview,
    build_colibri_batch,
    default_reviewer_roster,
    manual_reviewer_roster,
    render_manual_review_bundle,
    resolve_arbiter_experiment,
)


router = APIRouter()


@router.get("/api/council/status")
async def api_council_status():
    roster = default_reviewer_roster()
    covered = sorted({axis for spec in roster if spec.available for axis in spec.axes})
    return {
        "schema": "devin_council_status_v1",
        "mode": "review_only",
        "axes": AXES,
        "configured_reviewers": [spec.__dict__ for spec in roster],
        "manual_reviewer_templates": [spec.__dict__ for spec in manual_reviewer_roster()],
        "covered_axes": covered,
        "missing_axes": [axis for axis in AXES if axis not in covered],
        "semantic_models_started": False,
        "automatic_promotion": False,
        "colibri_model_agnostic": True,
    }


@router.post("/api/council/plans")
async def api_council_plan(request: Request):
    data = await request.json()
    try:
        roster = [ReviewerSpec.from_mapping(item) for item in data.get("reviewers", ())]
        if not roster:
            roster = default_reviewer_roster()
        budgeter = CapacityBudgeter(
            max_reviewers=data.get("max_reviewers", 7),
            total_tokens=data.get("total_tokens", 12_000),
            total_seconds=data.get("total_seconds", 360),
        )
        return CouncilRouter(budgeter).plan(
            data.get("evidence_packet", {}), roster,
            critical=bool(data.get("critical", False)),
            external_consent=bool(data.get("external_consent", False)),
        )
    except (TypeError, ValueError) as exc:
        return {"error": str(exc), "promotion_performed": False}


@router.post("/api/council/aggregate")
async def api_council_aggregate(request: Request):
    data = await request.json()
    try:
        verdicts = [ReviewVerdict.from_mapping(item) for item in data.get("verdicts", ())]
        return CouncilAggregator().aggregate(data.get("plan", {}), verdicts)
    except (TypeError, ValueError) as exc:
        return {"error": str(exc), "promotion_performed": False}


@router.post("/api/council/manual/bundle")
async def api_council_manual_bundle(request: Request):
    """Prepare copy/paste prompts; this endpoint never contacts a provider."""
    data = await request.json()
    try:
        return render_manual_review_bundle(data.get("plan", {}))
    except (TypeError, ValueError) as exc:
        return {"error": str(exc), "promotion_performed": False}


@router.post("/api/council/manual/prepare")
async def api_council_manual_prepare(request: Request):
    """Build a redacted preview or an operator-approved copy/paste bundle."""
    from devin.ui.routers.training import _training_store_for

    data = await request.json()
    store = _training_store_for(data.get("project_path", ""))
    attempt_id = str(data.get("attempt_id") or "").strip()
    attempts = {
        item.get("attempt_id"): item for item in store.list_attempts(limit=10_000)
    }
    attempt = attempts.get(attempt_id)
    if not attempt:
        return {"error": "known attempt_id is required", "promotion_performed": False}
    cases = {
        item.get("case_id"): item
        for item in store.list_cases(limit=10_000, include_retired=True)
    }
    try:
        preview = build_manual_evidence_preview(
            attempt,
            cases.get(attempt.get("case_id"), {}),
        )
        if data.get("redaction_approved") is not True:
            return {
                **preview,
                "approval_required": True,
                "promotion_performed": False,
            }
        manifest = {
            **preview["redaction_manifest"],
            "approved": True,
            "approved_by": "operator",
        }
        evidence = {
            **preview["packet"],
            "external_packet": preview["packet"],
            "redaction_manifest": manifest,
        }
        plan = CouncilRouter(CapacityBudgeter(
            max_reviewers=5,
            total_tokens=20_000,
            total_seconds=4_500,
        )).plan(
            evidence,
            manual_reviewer_roster(),
            external_consent=True,
        )
        return {
            "schema": "devin_manual_council_preparation_v1",
            "preview": {**preview, "redaction_manifest": manifest},
            "plan": plan,
            "bundle": render_manual_review_bundle(plan),
            "promotion_performed": False,
        }
    except (TypeError, ValueError) as exc:
        return {"error": str(exc), "promotion_performed": False}


@router.post("/api/council/colibri/batch")
async def api_council_colibri_batch(request: Request):
    """Prepare a stopped-by-default batch for any Colibri-supported model."""
    data = await request.json()
    try:
        runtime = ArbiterRuntimeIdentity.from_mapping(data.get("runtime", {}))
        return build_colibri_batch(data.get("results", ()), runtime)
    except (TypeError, ValueError) as exc:
        return {"error": str(exc), "promotion_performed": False}


@router.post("/api/council/arbiter/resolve")
async def api_council_arbiter_resolve(request: Request):
    data = await request.json()
    try:
        runtime_data = data.get("arbiter_runtime")
        runtime = (
            ArbiterRuntimeIdentity.from_mapping(runtime_data)
            if isinstance(runtime_data, dict) else None
        )
        return resolve_arbiter_experiment(
            axis=data.get("axis", ""),
            experiment=data.get("experiment", {}),
            experiment_result=data.get("experiment_result", {}),
            arbiter_runtime=runtime,
        )
    except (TypeError, ValueError) as exc:
        return {"error": str(exc), "promotion_performed": False}
