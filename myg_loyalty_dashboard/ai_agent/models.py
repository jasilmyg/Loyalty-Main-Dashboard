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



# ── Jazz AI (ClickHouse assistant) ────────────────────────────────────────────
class JazzChatLog(models.Model):
    """One Jazz AI question/answer turn: powers audit, history, cost tracking and feedback."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='jazz_chat_logs')
    session_id = models.CharField(max_length=80, blank=True, default='', db_index=True)
    question = models.TextField()
    answer = models.TextField(blank=True, default='')
    sql_list = models.JSONField(default=list, blank=True)       # SQL statements executed (in order)
    tools_used = models.JSONField(default=list, blank=True)     # agent names used
    model_used = models.CharField(max_length=80, blank=True, default='')
    elapsed_ms = models.IntegerField(default=0)
    rows_returned = models.IntegerField(default=0)
    cache_hits = models.IntegerField(default=0)
    had_error = models.BooleanField(default=False)
    error_text = models.TextField(blank=True, default='')
    had_file = models.BooleanField(default=False)
    rating = models.SmallIntegerField(null=True, blank=True)    # 1 = helpful, -1 = wrong/unhelpful
    feedback_comment = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'jazz_chat_logs'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user_id} {self.question[:60]}"


class JazzGoldenQuery(models.Model):
    """Verified question -> SQL pairs. Approved ones are retrieved as few-shot examples for similar questions."""
    question = models.TextField()
    sql = models.TextField()
    notes = models.TextField(blank=True, default='')
    approved = models.BooleanField(default=False, help_text='Only approved entries are shown to the model.')
    source_log = models.ForeignKey(JazzChatLog, null=True, blank=True, on_delete=models.SET_NULL, related_name='golden')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    times_used = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'jazz_golden_queries'
        ordering = ['-created_at']

    def __str__(self):
        return self.question[:80]


class JazzMetric(models.Model):
    """Semantic layer: business definitions injected into Jazz's prompt so KPIs are computed consistently."""
    name = models.CharField(max_length=120, unique=True)
    definition = models.TextField(help_text='Plain-language meaning of the KPI.')
    sql_hint = models.TextField(blank=True, default='', help_text='Table/column/filter rules or an SQL expression to use.')
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'jazz_metrics'
        ordering = ['name']

    def __str__(self):
        return self.name


class JazzSavedPrompt(models.Model):
    """User-starred prompts ("Saved" tab in the Jazz AI history drawer)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='jazz_saved_prompts')
    prompt = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'jazz_saved_prompts'
        ordering = ['-created_at']
        unique_together = [('user', 'prompt')]

    def __str__(self):
        return self.prompt[:60]
