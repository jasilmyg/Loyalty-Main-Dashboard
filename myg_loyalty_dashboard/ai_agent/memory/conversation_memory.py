from ..models import AIConversation, AIMessage

class ConversationMemory:
    @staticmethod
    def get_or_create_conversation(user, conversation_id=None):
        if conversation_id:
            try:
                return AIConversation.objects.get(id=conversation_id, user=user)
            except AIConversation.DoesNotExist:
                pass
        
        # Create a new conversation
        return AIConversation.objects.create(user=user)

    @staticmethod
    def save_interaction(conversation, user_prompt: str, ai_response: str):
        # Save user message
        AIMessage.objects.create(
            conversation=conversation,
            role='user',
            content=user_prompt
        )
        
        # Save AI message
        AIMessage.objects.create(
            conversation=conversation,
            role='ai',
            content=ai_response
        )
        
        # Update conversation timestamp
        conversation.save()

    @staticmethod
    def get_history(conversation):
        """Returns the chat history format needed for context (Last 5 messages)."""
        messages = list(conversation.messages.order_by('-id')[:10])
        messages.reverse()
        return [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]
