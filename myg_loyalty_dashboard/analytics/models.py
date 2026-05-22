from django.db import models


class ForecastCache(models.Model):
    """
    Stores the LSTM forecast cache data in the database so it's available
    on all environments (local, Render, etc.) without relying on a local file.
    """
    cache_key = models.CharField(max_length=64, unique=True, default='lstm_amj_2026')
    data = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Forecast Cache"
        verbose_name_plural = "Forecast Caches"

    def __str__(self):
        return f"{self.cache_key} (updated: {self.updated_at})"

    @classmethod
    def get_lstm_cache(cls):
        """Returns the LSTM forecast cache dict, or an empty fallback."""
        try:
            obj = cls.objects.get(cache_key='lstm_amj_2026')
            return obj.data
        except cls.DoesNotExist:
            return {"KPIs": {}, "Charts": {}, "Insights": []}

    @classmethod
    def set_lstm_cache(cls, data: dict):
        """Upserts the LSTM forecast cache with new data."""
        obj, _ = cls.objects.update_or_create(
            cache_key='lstm_amj_2026',
            defaults={'data': data}
        )
        return obj
