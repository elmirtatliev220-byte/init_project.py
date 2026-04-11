from pydantic import BaseModel
from typing import Optional, Union

class AIRequest(BaseModel):
    action: str
    prompt: str

class WalletRequest(BaseModel):
    address: str

class DeleteRequest(BaseModel):
    id: int

class ChannelAddRequest(BaseModel):
    tg_id: Union[int, str]

class AutoDistributeRequest(BaseModel):
    channel_id: int

class MovePostRequest(BaseModel):
    post_id: int
    new_date: str # YYYY-MM-DD

class ChannelCheckRequest(BaseModel):
    link: str

class ProtectionRequest(BaseModel):
    chat_id: Union[int, str]

class ChannelDeleteRequest(BaseModel):
    channel_id: int

class ClearQueueRequest(BaseModel):
    channel_id: int

class AdRequest(BaseModel):
    title: str
    description: str
    link: str

class LoginRequest(BaseModel):
    initData: str

class InviteRequest(BaseModel):
    channel_id: int
    name: str

class AutoResponseAddRequest(BaseModel):
    channel_id: int
    trigger: str
    response: str
