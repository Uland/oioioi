import os.path
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import ugettext as _
from django.db import transaction
from oioioi.problems.package import backend_for_package, NoBackend
from oioioi.base.utils import get_object_by_dotted_name


class Command(BaseCommand):
    args = _("<filename>")
    help = _("Adds the problem from the given package to the database.")

    @transaction.atomic
    def handle(self, *args, **options):
        if not args:
            raise CommandError(_("Missing argument (filename)"))
        if len(args) > 1:
            raise CommandError(_("Expected only one argument"))

        filename = args[0]
        if not os.path.exists(filename):
            raise CommandError(_("File not found: ") + filename)
        try:
            backend = \
                    get_object_by_dotted_name(backend_for_package(filename))()
        except NoBackend:
            raise CommandError(_("Package format not recognized"))

        problem = backend.simple_unpack(filename)
        self.stdout.write('%d\n' % (problem.id,))
