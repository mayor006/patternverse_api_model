"""Pattern routes: list a user's confirmed patterns."""

from fastapi import APIRouter

from models.schemas import PatternObject, PatternsResponse
from services.supabase_service import get_store

router = APIRouter(tags=["patterns"])


@router.get("/patterns/{user_id}", response_model=PatternsResponse)
async def get_patterns(user_id: str) -> PatternsResponse:
    store = get_store()
    rows = await store.get_patterns_by_user(user_id)
    patterns = [
        PatternObject(
            pattern_name=r["pattern_name"],
            pattern_summary=r["pattern_summary"],
            trigger=r["trigger"],
            response=r["response"],
            insight=r["insight"],
            next_step=r["next_step"],
        )
        for r in rows
    ]
    return PatternsResponse(patterns=patterns)
