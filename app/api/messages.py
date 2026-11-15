"""Messages API router for incoming chat handling."""

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.api.security import verify_api_key
from app.services.message_service import MessageService

router = APIRouter(tags=["Messages"], dependencies=[Depends(verify_api_key)])


class MessageRequest(BaseModel):
    """Schema for incoming message webhook payload."""

    channel: str
    chat_id: str
    text: str | None = None
    image_base64: str | None = None


class MessageResponse(BaseModel):
    """Schema for message processing response."""

    intent: str
    reply_text: str
    pending_transaction_id: int | None = None
    show_main_menu: bool = False


@router.post(
    "/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Process incoming chat message and draft transactions",
)
def handle_incoming_message(payload: MessageRequest) -> MessageResponse:
    """Handle incoming message, perform routing and extraction, return response."""
    service = MessageService()
    result = service.process_message(
        channel=payload.channel,
        chat_id=payload.chat_id,
        text=payload.text,
        image_base64=payload.image_base64,
    )
    return MessageResponse(**result)
