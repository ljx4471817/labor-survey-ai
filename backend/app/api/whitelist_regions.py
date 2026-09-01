# -*- coding: utf-8 -*-
"""Standard survey-point options for the whitelist entry form."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.infra.auth import region_scope, require_whitelist_admin
from app.services.region_points import filter_region_points, load_region_points

router = APIRouter()


@router.get("/whitelist/region-points")
def list_region_points(user: dict = Depends(require_whitelist_admin)) -> dict:
    """Return survey points scoped to the current whitelist administrator."""
    points = filter_region_points(load_region_points(), region_scope(user))
    return {
        "ok": True,
        "default_province": "贵州省",
        "points": points,
        "count": len(points),
    }
