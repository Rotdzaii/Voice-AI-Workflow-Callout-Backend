from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..dependencies import get_current_user
from ..supabase_client import get_client

router = APIRouter(prefix="/workflows", tags=["workflows"])
logger = logging.getLogger(__name__)


class WorkflowBase(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = Field(default="draft")
    nodes: List[Dict[str, Any]] = Field(default_factory=list)
    edges: List[Dict[str, Any]] = Field(default_factory=list)


class WorkflowCreate(WorkflowBase):
    name: str


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    status: Optional[str] = None
    user_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    nodes: List[Dict[str, Any]] = Field(default_factory=list)
    edges: List[Dict[str, Any]] = Field(default_factory=list)


def _get_supabase_client(*, required: bool = True):
    client = get_client()
    if client:
        return client
    if required:
        detail = (
            "Supabase client not configured. Set SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY "
            "and apply db/voiceai_schema_combined.sql to create the workflows table."
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
    return None


async def _execute(builder):
    try:
        return await asyncio.to_thread(builder.execute)
    except Exception as exc:  # pragma: no cover - bubble up as HTTP error
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Supabase error: {exc}") from exc


def _serialize_workflow(record: Dict[str, Any]) -> Dict[str, Any]:
    workflow_json = record.get("workflow_json") or {}
    return {
        "id": str(record.get("id")),
        "name": record.get("name") or "Untitled Workflow",
        "description": record.get("description"),
        "status": record.get("status"),
        "user_id": record.get("user_id"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "nodes": workflow_json.get("nodes") or [],
        "edges": workflow_json.get("edges") or [],
    }


async def _fetch_workflow(client, workflow_id: str, user_id: str) -> Dict[str, Any]:
    builder = (
        client.table("workflows")
        .select("*")
        .eq("id", workflow_id)
        .eq("user_id", user_id)
        .limit(1)
    )
    response = await _execute(builder)
    records = response.data or []
    if not records:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return records[0]


def _merge_workflow_json(existing: Dict[str, Any], nodes: Optional[List[Dict[str, Any]]], edges: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    current = existing.get("workflow_json") or {}
    return {
        "nodes": nodes if nodes is not None else current.get("nodes") or [],
        "edges": edges if edges is not None else current.get("edges") or [],
    }


@router.get("/", response_model=List[WorkflowResponse])
async def list_workflows(current_user: dict = Depends(get_current_user)):
    client = _get_supabase_client(required=False)
    if not client:
        return JSONResponse(content=[], headers={"X-System-Status": "degraded"})
    builder = (
        client.table("workflows")
        .select("*")
        .eq("user_id", current_user["id"])
        .order("created_at", desc=True)
    )
    try:
        response = await _execute(builder)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_502_BAD_GATEWAY:
            logger.warning("Supabase unavailable while listing workflows; returning empty list")
            return JSONResponse(content=[], headers={"X-System-Status": "degraded"})
        raise
    return [_serialize_workflow(item) for item in response.data or []]


@router.post("/", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(payload: WorkflowCreate, current_user: dict = Depends(get_current_user)):
    client = _get_supabase_client()
    insert_payload: Dict[str, Any] = {
        "name": payload.name.strip() or "Untitled Workflow",
        "description": payload.description,
        "status": payload.status or "draft",
        "user_id": current_user["id"],
        "workflow_json": {
            "nodes": payload.nodes,
            "edges": payload.edges,
        },
    }
    builder = client.table("workflows").insert(insert_payload).select("*")
    response = await _execute(builder)
    records = response.data or []
    if not records:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create workflow")
    return _serialize_workflow(records[0])


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: str, current_user: dict = Depends(get_current_user)):
    client = _get_supabase_client()
    record = await _fetch_workflow(client, workflow_id, current_user["id"])
    return _serialize_workflow(record)


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def save_workflow(
    workflow_id: str,
    payload: WorkflowUpdate,
    current_user: dict = Depends(get_current_user),
):
    client = _get_supabase_client()
    existing = await _fetch_workflow(client, workflow_id, current_user["id"])

    updates: Dict[str, Any] = {}
    if payload.name is not None:
        updates["name"] = payload.name.strip() or existing.get("name") or "Untitled Workflow"
    if payload.description is not None:
        updates["description"] = payload.description
    if payload.status is not None:
        updates["status"] = payload.status
    if payload.nodes is not None or payload.edges is not None:
        updates["workflow_json"] = _merge_workflow_json(existing, payload.nodes, payload.edges)

    if not updates:
        return _serialize_workflow(existing)

    builder = (
        client.table("workflows")
        .update(updates)
        .eq("id", workflow_id)
        .eq("user_id", current_user["id"])
        .select("*")
    )
    response = await _execute(builder)
    records = response.data or []
    if not records:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update workflow")
    return _serialize_workflow(records[0])


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str, current_user: dict = Depends(get_current_user)):
    client = _get_supabase_client()
    builder = (
        client.table("workflows")
        .delete()
        .eq("id", workflow_id)
        .eq("user_id", current_user["id"])
        .select("id")
    )
    response = await _execute(builder)
    records = response.data or []
    if not records:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return {"id": records[0].get("id"), "deleted": True}
