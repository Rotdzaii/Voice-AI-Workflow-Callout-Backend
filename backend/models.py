from pydantic import BaseModel
from typing import Optional, Dict, Any
from uuid import UUID


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: Optional[UUID]
    email: str
    role: Optional[str]


class SSOIn(BaseModel):
    email: str
    provider: str | None = None
    provider_id: str | None = None


class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str]
    workflow_json: Dict[str, Any]


class WorkflowOut(BaseModel):
    id: Optional[UUID]
    user_id: Optional[UUID]
    name: str
    description: Optional[str]
    status: Optional[str]


class WorkflowUpdate(BaseModel):
    name: Optional[str]
    description: Optional[str]
    status: Optional[str]
    workflow_json: Optional[Dict[str, Any]]


class NLUParseIn(BaseModel):
    text: str


class NLUParseOut(BaseModel):
    intent: Dict[str, Any]
    entities: Any


class ConversationIn(BaseModel):
    call_id: str
    speaker: str
    text: str


class ConversationOut(BaseModel):
    call_id: str
    turn: int
    nlu: Dict[str, Any]
    reply: str


# JSON body models to ease frontend integration
class CallStartIn(BaseModel):
    workflow_id: str
    customer_phone: str


class CallReplyIn(BaseModel):
    call_id: str
    speaker: str
    text: str


class ConversationAgentIn(BaseModel):
    text: str
    call_id: Optional[str] = None


class RagQueryIn(BaseModel):
    question: str
    k: Optional[int] = None
    group: Optional[str] = None
    topic: Optional[str] = None


class RagQueryOut(BaseModel):
    query_id: Optional[str] = None
    answer: str
    source_ids: Optional[list] = None
    groups: Optional[list] = None
    topics: Optional[list] = None
    scores: Optional[list] = None
    # Optional latency fields for observability
    latency_total: Optional[float] = None
    latency_retriever: Optional[float] = None
    latency_context: Optional[float] = None
    latency_prompt: Optional[float] = None
    latency_llm: Optional[float] = None


class TTSIn(BaseModel):
    text: str
    base_filename: Optional[str] = None
    query_id: Optional[str] = None


class TTSOut(BaseModel):
    ok: bool
    tts_id: Optional[str] = None
    audio_base64: Optional[str] = None
    audio_path: Optional[str] = None
    latency: Optional[float] = None
    error: Optional[str] = None

