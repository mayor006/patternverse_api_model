"""Growth Area insight route.

A single inference endpoint that scores the six personal-growth dimensions from
a user's profile/progress data. The Supabase ``growth-area-insights`` edge
function POSTs the §3 payload here in place of its old Claude call; on any model
failure this endpoint transparently falls back to the deterministic heuristic,
so the card always renders.
"""

import logging

from fastapi import APIRouter

from models.schemas import GrowthAreaRequest, GrowthAreaResponse
from services import growth_area

logger = logging.getLogger("patternverse.growth_area")

router = APIRouter(prefix="/insights", tags=["insights"])


@router.post("/growth-area", response_model=GrowthAreaResponse)
async def growth_area_insights(req: GrowthAreaRequest) -> GrowthAreaResponse:
    areas, source = await growth_area.score_growth_area(req)
    logger.info("Growth-area scored via %s.", source)
    return GrowthAreaResponse(areas=growth_area.to_area_scores(areas), source=source)
