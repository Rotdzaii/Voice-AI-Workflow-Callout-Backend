from fastapi import APIRouter

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("/")
async def list_workflows():
    """Placeholder for workflow listing"""
    return {"workflows": []}
