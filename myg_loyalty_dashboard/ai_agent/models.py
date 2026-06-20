from django.db import models
from django.conf import settings
from pgvector.django import VectorField

class AIConversation(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ai_conversations')
    title = models.CharField(max_length=255, default="New Conversation")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ai_conversations'
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.user.username} - {self.title}"


class AIMessage(models.Model):
    ROLE_CHOICES = (
        ('user', 'User'),
        ('ai', 'AI Agent')
    )
    conversation = models.ForeignKey(AIConversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    reasoning_details = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ai_messages'
        ordering = ['created_at']

    def __str__(self):
        return f"[{self.role}] {self.content[:50]}"


class AIFavorite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ai_favorites')
    prompt = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ai_favorites'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} Fav: {self.prompt[:50]}"


class AIAuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ai_audit_logs')
    prompt = models.TextField()
    matched_agent = models.CharField(max_length=100)
    generated_sql = models.TextField(blank=True, null=True)
    execution_time_ms = models.IntegerField(default=0)
    tokens_used = models.IntegerField(default=0)
    cache_hit = models.BooleanField(default=False)
    is_async = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ai_audit_logs'
        ordering = ['-created_at']

    def __str__(self):
        hit = "CACHE HIT" if self.cache_hit else "MISS"
        return f"[{self.matched_agent}] {hit} - {self.execution_time_ms}ms"

class SchemaVector(models.Model):
    table_name = models.CharField(max_length=255, unique=True)
    description = models.TextField()
    columns_json = models.JSONField(default=list)
    embedding = VectorField(dimensions=1024)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ai_schema_vectors'

    def __str__(self):
        return self.table_name

