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


class ProductSale(models.Model):
    """
    Stores raw sales data needed for analytics and report generation.
    """
    date = models.DateField(db_index=True)
    invoice_number = models.CharField(max_length=100)
    branch = models.CharField(max_length=100)
    
    # Product details
    product = models.CharField(max_length=100, db_index=True)
    category = models.CharField(max_length=100, db_index=True)
    brand = models.CharField(max_length=100, db_index=True)
    
    # Financials
    qty = models.IntegerField(default=1)
    sold_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Product Sale"
        verbose_name_plural = "Product Sales"
        # Index to speed up monthly queries
        indexes = [
            models.Index(fields=['date', 'product']),
        ]

    def __str__(self):
        return f"{self.invoice_number} - {self.product} ({self.date})"


class SheStartCandidateScore(models.Model):
    """
    Stores manually overridden scores for candidates in the She Start dashboard.
    These values override the dummy/default values from Google Sheets.
    """
    candidate_name = models.CharField(max_length=255, unique=True, db_index=True)
    interview = models.FloatField(null=True, blank=True)
    growth = models.FloatField(null=True, blank=True)
    need = models.FloatField(null=True, blank=True)
    emotional = models.FloatField(null=True, blank=True)
    sustainability = models.FloatField(null=True, blank=True)
    utilization = models.FloatField(null=True, blank=True)
    
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "She Start Score Override"
        verbose_name_plural = "She Start Score Overrides"

    def __str__(self):
        return f"{self.candidate_name} Overrides"
