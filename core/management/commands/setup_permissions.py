from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from guardian.shortcuts import assign_perm
from core.models import Message, DataSource

class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        user = User.objects.create_user('testuser', 'test@example.com', 'password')
        for msg in Message.objects.all():
            assign_perm('view_message', user, msg)
        for ds in DataSource.objects.all():
            assign_perm('view_datasource', user, ds)
        self.stdout.write(self.style.SUCCESS('Permissions assigned'))