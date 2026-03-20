from django.core.management.base import BaseCommand
from core.tasks import calculate_project_risk

class Command(BaseCommand):
    help = 'Manually trigger risk calculation for all projects'

    def handle(self, *args, **kwargs):
        result = calculate_project_risk()
        self.stdout.write(self.style.SUCCESS(result))