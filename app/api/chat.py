from fastapi import APIRouter

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

router = APIRouter(
    prefix="/chat",
    tags=["AI Chat"]
)

service = ChatService()


@router.post("/", response_model=ChatResponse)
def chat(request: ChatRequest):

    answer = service.chat(request.history, request.reference_history)

    return ChatResponse(response=answer)
