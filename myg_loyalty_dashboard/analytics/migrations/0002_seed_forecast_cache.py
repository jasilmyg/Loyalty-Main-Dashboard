"""
Data migration: seed the ForecastCache table with the AMJ 2026 LSTM forecast.

The full cache payload is embedded directly here so `python manage.py migrate`
on Render (or any fresh environment) populates the DB without needing the
gitignored analytics/lstm_forecast_cache.json file.
"""
from django.db import migrations

LSTM_CACHE_DATA = {
    "KPIs": {
        "Total_DB": 5033297,
        "Target_Repeat": 402663,
        "Achieved_Repeat": 210302,
        "Achieved_Pct": 52.22779346500672,
        "Gap": 192361,
        "Forecast_Final": 378728,
        "Forecast_Confidence": 92.4,
        "Prob_Target": 94.0559074853399,
        "Current_Run_Rate": 4474,
        "Required_Run_Rate": 4371,
        "Momentum_Score": 86.5,
        "Retention_Health": 91.2,
        "Festival_Impact": 114.5,
        "Seasonal_Momentum": 1.12,
        "Metrics": {
            "RMSE": "412.3 (Daily)",
            "MAE": "298.7 (Daily)",
            "MAPE": "4.9%",
            "R2": "0.924"
        }
    },
    "Charts": {
        "BurnUp": {
            "Actual_Dates": [
                "2026-04-01","2026-04-02","2026-04-03","2026-04-04","2026-04-05",
                "2026-04-06","2026-04-07","2026-04-08","2026-04-09","2026-04-10",
                "2026-04-11","2026-04-12","2026-04-13","2026-04-14","2026-04-15",
                "2026-04-16","2026-04-17","2026-04-18","2026-04-19","2026-04-20",
                "2026-04-21","2026-04-22","2026-04-23","2026-04-24","2026-04-25",
                "2026-04-26","2026-04-27","2026-04-28","2026-04-29","2026-04-30",
                "2026-05-01","2026-05-02","2026-05-03","2026-05-04","2026-05-05",
                "2026-05-06","2026-05-07","2026-05-08","2026-05-09","2026-05-10",
                "2026-05-11","2026-05-12","2026-05-13","2026-05-14","2026-05-15",
                "2026-05-16","2026-05-17"
            ],
            "Actual_Vals": [
                4222,8290,12364,17519,22394,26570,29862,33604,37492,41757,
                47038,52681,57670,62796,67376,71515,75691,81037,86615,91853,
                96532,99962,103963,108146,113210,118086,122163,126310,130621,134722,
                139003,143827,148732,153036,157227,161393,165643,170071,175009,179922,
                184187,188439,192708,196350,200361,205391,210301
            ],
            "Forecast_Dates": [
                "2026-05-18","2026-05-19","2026-05-20","2026-05-21","2026-05-22",
                "2026-05-23","2026-05-24","2026-05-25","2026-05-26","2026-05-27",
                "2026-05-28","2026-05-29","2026-05-30","2026-05-31","2026-06-01",
                "2026-06-02","2026-06-03","2026-06-04","2026-06-05","2026-06-06",
                "2026-06-07","2026-06-08","2026-06-09","2026-06-10","2026-06-11",
                "2026-06-12","2026-06-13","2026-06-14","2026-06-15","2026-06-16",
                "2026-06-17","2026-06-18","2026-06-19","2026-06-20","2026-06-21",
                "2026-06-22","2026-06-23","2026-06-24","2026-06-25","2026-06-26",
                "2026-06-27","2026-06-28","2026-06-29","2026-06-30"
            ],
            "Forecast_Vals": [
                214641,218981,223321,226649,230308,234301,238627,243285,248275,252424,
                256243,260098,264180,268715,273823,278069,281900,284933,288406,291652,
                294899,298718,302346,305592,309220,312467,316286,320105,323733,326979,
                330607,334235,337864,341492,345311,348939,352758,356386,360205,364024,
                367843,371472,375100,378728
            ],
            "Upper_95": [
                214685,219069,223453,226824,230527,234564,238933,243635,248670,252862,
                256725,260624,264749,269328,274480,278770,282645,285721,289239,292529,
                295819,299682,303353,306644,310315,313606,317468,321331,325003,328293,
                331965,335637,339309,342981,346844,350516,354379,358051,361914,365777,
                369640,373311,376983,380655
            ],
            "Lower_95": [
                214598,218894,223190,226473,230089,234038,238320,242934,247881,251986,
                255761,259572,263610,268101,273166,277368,281155,284144,287574,290776,
                293979,297754,301338,304541,308125,311328,315103,318878,322462,325665,
                329249,332834,336418,340002,343777,347362,351137,354721,358497,362272,
                366047,369632,373216,376800
            ],
            "Target": 402663,
            "Min_Required": 362396
        }
    },
    "Insights": [
        "Bakrid (May 26, 2026) falls inside the active AMJ forecast window - the model has integrated a +15% repeat customer surge for this period.",
        "Akshaya Tritiya (Apr 20, 2026) produced a verified spike in early AMJ actuals; the Attention mechanism focuses directly on this temporal spike.",
        "School Reopening (June 1) historically drives sustained purchase frequency; forecast confidence increases for June period.",
        "Monthly salary cycles (1st-5th) are modeled with a +7% repeat conversion lift, compounding the natural base run rate of 4,474/day.",
        "South-West Monsoon onset (early June) brings heavy rainfall (>20mm), introducing a temporary -15% retail footfall dip in the prediction layer.",
        "Summer temperatures (>33C in Apr/May) are modeled with a -7% afternoon slump, successfully captured by the BiLSTM's bidirectional context layers.",
        "Based on Kerala's full festival and weather intelligence engine, the BiLSTM with Attention predicts a final AMJ repeat achievement of ~378,728 customers."
    ],
    "FestivalEngine": {
        "Version": "Level 2",
        "Feature_Dimensions": 12,
        "Features": [
            "lag_1","lag_7","lag_30","rolling_7","is_festival",
            "days_before_festival","days_after_festival","festival_weight",
            "is_salary_period","temperature","rainfall","humidity"
        ],
        "Festival_Count": 605,
        "Active_In_Horizon": ["Bakrid", "School Reopening"]
    }
}


def seed_forecast_cache(apps, schema_editor):
    ForecastCache = apps.get_model('analytics', 'ForecastCache')
    ForecastCache.objects.update_or_create(
        cache_key='lstm_amj_2026',
        defaults={'data': LSTM_CACHE_DATA}
    )


def unseed_forecast_cache(apps, schema_editor):
    ForecastCache = apps.get_model('analytics', 'ForecastCache')
    ForecastCache.objects.filter(cache_key='lstm_amj_2026').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0001_add_forecast_cache'),
    ]

    operations = [
        migrations.RunPython(seed_forecast_cache, unseed_forecast_cache),
    ]
