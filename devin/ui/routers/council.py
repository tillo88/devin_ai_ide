"""P6 Federated Council planning and aggregation API (no model execution)."""

from fastapi import APIRouter, Request

from devin.training.federated_council import (
    AXES,
    CapacityBudgeter,
    CouncilAggregator,
    CouncilRouter,
    ReviewVerdict,
    ReviewerSpec,
    default_reviewer_roster,
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
        "covered_axes": covered,
        "missing_axes": [axis for axis in AXES if axis not in covered],
        "semantic_models_started": False,
        "automatic_promotion": False,
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


@router.post("/api/council/arbiter/resolve")
async def api_council_arbiter_resolve(request: Request):
    data = await request.json()
    try:
        return resolve_arbiter_experiment(
            axis=data.get("axis", ""),
            experiment=data.get("experiment", {}),
            experiment_result=data.get("experiment_result", {}),
        )
    except (TypeError, ValueError) as exc:
        return {"error": str(exc), "promotion_performed": False}
