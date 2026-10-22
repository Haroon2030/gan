from django.core.management.base import BaseCommand

from search.oracle_link import restore_link_files


class Command(BaseCommand):
    help = 'استعادة مفاتيح وكيل الربط من القاعدة إلى مجلد sshd قبل تشغيله.'

    def handle(self, *args, **options):
        source = restore_link_files()
        labels = {
            'database': 'من القاعدة',
            'disk': 'من القرص وحُفظ في القاعدة',
            'generated': 'مفتاح خادم جديد وحُفظ في القاعدة',
        }
        self.stdout.write(self.style.SUCCESS(f'مفاتيح وكيل الربط: {labels[source]}.'))
