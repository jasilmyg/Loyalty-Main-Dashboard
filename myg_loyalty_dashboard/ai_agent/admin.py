from django.contrib import admin

from .models import JazzChatLog, JazzGoldenQuery, JazzMetric, JazzSavedPrompt


@admin.register(JazzChatLog)
class JazzChatLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "short_q", "model_used", "elapsed_ms", "rows_returned", "cache_hits", "had_error", "rating")
    list_filter = ("had_error", "rating", "model_used", "had_file", "created_at")
    search_fields = ("question", "answer", "user__username", "error_text")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"
    actions = ["promote_to_golden"]

    @admin.display(description="Question")
    def short_q(self, obj):
        return obj.question[:90]

    @admin.action(description="Promote selected answers to approved golden queries")
    def promote_to_golden(self, request, queryset):
        n = 0
        for log in queryset:
            sql = (log.sql_list or [""])[-1] if log.sql_list else ""
            if sql and not JazzGoldenQuery.objects.filter(source_log=log).exists():
                JazzGoldenQuery.objects.create(question=log.question, sql=sql, approved=True, source_log=log, created_by=request.user)
                n += 1
        self.message_user(request, f"{n} golden quer{'y' if n == 1 else 'ies'} created.")


@admin.register(JazzGoldenQuery)
class JazzGoldenQueryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "short_q", "approved", "times_used", "created_by")
    list_filter = ("approved",)
    list_editable = ("approved",)
    search_fields = ("question", "sql")

    @admin.display(description="Question")
    def short_q(self, obj):
        return obj.question[:90]


@admin.register(JazzMetric)
class JazzMetricAdmin(admin.ModelAdmin):
    list_display = ("name", "active", "updated_at")
    list_filter = ("active",)
    search_fields = ("name", "definition")


@admin.register(JazzSavedPrompt)
class JazzSavedPromptAdmin(admin.ModelAdmin):
    list_display = ("user", "prompt", "created_at")
