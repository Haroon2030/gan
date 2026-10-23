import os
import re

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'إنشاء أول حساب مدير من متغيرات البيئة عند خلو قاعدة البيانات من المستخدمين فقط. '
        'بعدها تُدار الحسابات كلها من شاشة المستخدمين ولا يلمسها هذا الأمر.'
    )

    def handle(self, *args, **options):
        user_model = get_user_model()

        if user_model.objects.exists():
            self.stdout.write('يوجد مستخدمون؛ الحسابات تُدار من شاشة المستخدمين.')
            return

        username = os.environ.get('APP_LOGIN_USERNAME', '').strip()
        password = os.environ.get('APP_LOGIN_PASSWORD', '')

        if not username or not password:
            self.stdout.write(
                self.style.WARNING(
                    'لا مستخدمين ولم تُضبط APP_LOGIN_USERNAME/APP_LOGIN_PASSWORD؛ لم يُنشأ حساب.'
                )
            )
            return

        if len(password) < 6:
            self.stderr.write(
                self.style.ERROR('APP_LOGIN_PASSWORD يجب ألا تقل عن 6 أحرف.')
            )
            return

        user = user_model.objects.create_user(
            username=username,
            password=password,
            first_name=username,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.stdout.write(self.style.SUCCESS(f'تم إنشاء أول حساب مدير: {username}'))

        from search.models import UserProfile

        if re.fullmatch(r'[0-9+\-]{7,20}', username):
            phone = username
        else:
            phone = f'05{user.pk:08d}'

        UserProfile.objects.get_or_create(
            user=user,
            defaults={
                'display_name': user.first_name or username,
                'phone': phone[:20],
                'role_name': 'سوبر أدمن',
            },
        )
