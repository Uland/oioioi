from django.core.management.base import NoArgsCommand
from django.utils.translation import ugettext as _
from oioioi.oi.models import School
from oioioi.oi.management.commands.import_schools import COLUMNS
import csv


class Command(NoArgsCommand):
    help = _("Exports schools list to a CSV file")

    requires_model_validation = True

    def handle_noargs(self, **options):
        writer = csv.writer(self.stdout)
        writer.writerow(COLUMNS)
        for school in School.objects.order_by('postal_code'):
            row = [unicode(getattr(school, column)).encode('utf-8')
                    for column in COLUMNS]
            writer.writerow(row)
