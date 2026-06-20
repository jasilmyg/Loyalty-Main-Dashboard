import json
from ..models import AIConversation, AIMessage


class ConversationMemory:
    """
    Stores and retrieves multi-turn conversation state.
    Critically, assistant messages preserve reasoning_details UNMODIFIED
    so the Nemotron model can resume its chain-of-thought on the next turn.
    """

    @staticmethod
    def get_or_create_conversation(user, conversation_id=None):
        if conversation_id:
            try:
                return AIConversation.objects.get(id=conversation_id, user=user)
            except AIConversation.DoesNotExist:
                pass
        return AIConversation.objects.create(user=user)

    @staticmethod
    def save_interaction(conversation, user_prompt: str, ai_response: str, reasoning_details=None):
        """Save a user+assistant turn to DB."""
        # User message
        AIMessage.objects.create(
            conversation=conversation,
            role='user',
            content=user_prompt
        )

        # Assistant message — always store reasoning_details as JSON
        AIMessage.objects.create(
            conversation=conversation,
            role='ai',
            content=ai_response,
            reasoning_details=reasoning_details if isinstance(reasoning_details, list)
                              else ([reasoning_details] if reasoning_details else None)
        )

        conversation.save()

    @staticmethod
    def get_history(conversation) -> list:
        """
        Returns the last 10 messages in OpenRouter multi-turn format.

        CRITICAL: For assistant messages that have reasoning_details,
        those are included UNMODIFIED so Nemotron can continue its
        chain-of-thought from where it left off on the next turn.

        Format:
            [
                {"role": "user",      "content": "..."},
                {"role": "assistant", "content": "...", "reasoning_details": [...]},
                {"role": "user",      "content": "..."},
                ...
            ]
        """
        messages = list(conversation.messages.order_by('-id')[:10])
        messages.reverse()

        history = []
        for msg in messages:
            entry = {
                "role":    "assistant" if msg.role == "ai" else "user",
                "content": msg.content or ""
            }

            # Preserve reasoning_details UNMODIFIED for assistant messages
            if msg.role == "ai" and msg.reasoning_details:
                rd = msg.reasoning_details
                # Ensure it's a list (as OpenRouter expects)
                if isinstance(rd, str):
                    try:
                        rd = json.loads(rd)
                    except Exception:
                        rd = [{"type": "thinking", "thinking": rd}]
                elif isinstance(rd, dict):
                    rd = [rd]
                entry["reasoning_details"] = rd

            history.append(entry)

        return history
