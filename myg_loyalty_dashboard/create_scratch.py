with open('daily_sales_predictor.py', 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace("'2026-06-01'", "'2026-08-25'")
text = text.replace("'2026-06-07'", "'2026-08-25'")

with open('scratch_predict_today.py', 'w', encoding='utf-8') as f:
    f.write(text)
print('Created scratch_predict_today.py')
