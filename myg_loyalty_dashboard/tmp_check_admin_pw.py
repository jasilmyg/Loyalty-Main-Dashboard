import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myg_loyalty_dashboard.settings')
import django
django.setup()
from django.contrib.auth import get_user_model
User = get_user_model()
user = User.objects.filter(username='admin').first()
if not user:
    print('NO_ADMIN')
else:
    candidates = ['admin','admin123','password','password123','123456','admin@123','Test1234','qwerty','letmein','test123']
    match = next((pwd for pwd in candidates if user.check_password(pwd)), None)
    print(match or 'NO_MATCH')
