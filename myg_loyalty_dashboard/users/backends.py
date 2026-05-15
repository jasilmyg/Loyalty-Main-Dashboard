import os
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth import get_user_model

class EnvAuthBackend(BaseBackend):
    """
    Authenticate against environment variables for multiple hardcoded users.
    """
    def authenticate(self, request, username=None, password=None):
        admin_username = os.environ.get('ADMIN_USERNAME', 'mygadmin')
        admin_password = os.environ.get('ADMIN_PASSWORD', 'myg@123')
        
        user_username = os.environ.get('USER_USERNAME', 'myguser')
        user_password = os.environ.get('USER_PASSWORD', 'user@123')
        
        User = get_user_model()
        
        if username == admin_username and password == admin_password:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                user = User(username=username, is_staff=True, is_superuser=True)
                user.set_unusable_password()
                user.save()
            return user
            
        if username == user_username and password == user_password:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                user = User(username=username, is_staff=False, is_superuser=False)
                user.set_unusable_password()
                user.save()
            return user
            
        return None

    def get_user(self, user_id):
        User = get_user_model()
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
