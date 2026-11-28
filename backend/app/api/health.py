from fastapi import APIRouter

from app.api.deps import SettingsDep

router = APIRouter(tags=["system"])


@router.get("/health")
def health(settings: SettingsDep) -> dict:
    return {"status": "ok", "llm_configured": settings.llm_configured}
