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

