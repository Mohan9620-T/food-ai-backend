from fastapi import APIRouter, Depends

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService
from app.utils.auth_dependency import get_current_user

router = APIRouter(
    prefix="/chat",
    tags=["AI Chat"]
)

service = ChatService()


@router.post("/", response_model=ChatResponse)
def chat(request: ChatRequest, current_user: dict = Depends(get_current_user)):

    answer = service.chat(request.history, request.reference_history)

    return ChatResponse(response=answer)