"""P8 routing profile inspection and planning API."""

from fastapi import APIRouter, Request

from devin.ai.capability_router import assess_canary, load_routing_profile, plan_capability_route


router = APIRouter()


@router.get("/api/routing/status")
async def api_routing_status():
    try:
        profile = load_routing_profile()
    except (OSError, ValueError) as exc:
        return {"error": str(exc), "automatic_switch": False}
    return {
        "schema": "devin_routing_status_v1",
        "profile_id": profile.profile_id,
        "profile_fingerprint": profile.fingerprint,
        "automatic_switch": False,
        "roles": {
            role: {
                "enabled": bool(config.get("enabled")),
                "future": bool(config.get("future", False)),
                "capabilities": config.get("capabilities", []),
            }
            for role, config in profile.roles.items()
        },
    }


@router.post("/api/routing/plan")
async def api_routing_plan(request: Request):
    data = await request.json()
    try:
        return plan_capability_route(
            data.get("capability", ""), resident_role=data.get("resident_role") or None,
        )
    except (OSError, TypeError, ValueError) as exc:
        return {"error": str(exc), "automatic_switch": False}


@router.post("/api/routing/canary/assess")
async def api_routing_canary_assess(request: Request):
    data = await request.json()
    try:
        return assess_canary(
            data.get("receipts", ()), role=data.get("role", ""),
            capability=data.get("capability", ""),
        )
    except (OSError, TypeError, ValueError) as exc:
        return {"error": str(exc), "automatic_switch": False, "automatic_promotion": False}
