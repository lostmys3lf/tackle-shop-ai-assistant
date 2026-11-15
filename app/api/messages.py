"""Messages API router for incoming chat handling."""

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.api.security import verify_api_key
from app.services.message_service import MessageService

router = APIRouter(tags=["Messages"], dependencies=[Depends(verify_api_key)])

MAX_CHANNEL_LENGTH = 32
MAX_CHAT_ID_LENGTH = 64
MAX_TEXT_LENGTH = 4000
MAX_IMAGE_BASE64_LENGTH = 10 * 1024 * 1024


class MessageRequest(BaseModel):
    """Schema for incoming message webhook payload."""

    channel: str = Field(max_length=MAX_CHANNEL_LENGTH)
    chat_id: str = Field(max_length=MAX_CHAT_ID_LENGTH)
    text: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    image_base64: str | None = Field(default=None, max_length=MAX_IMAGE_BASE64_LENGTH)


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
