"""مصادقة بالاسم أو الرقم أو اسم الدخول."""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q

# الأرقام العربية/الفارسية ٠-٩ و ۰-۹ تُطبع كثيراً من لوحة مفاتيح عربية —
# نوحّدها إلى أرقام إنجليزية قبل المطابقة حتى لا يفشل الدخول بسبب التخطيط.
_DIGIT_TABLE = str.maketrans(
    '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹',
    '01234567890123456789',
)


def _ascii_digits(value: str) -> str:
    return value.translate(_DIGIT_TABLE)


class UsernameOrPhoneBackend(ModelBackend):
    """يقبل الدخول بالرقم أو الاسم أو اسم المستخدم."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if not username or password is None:
            return None

        user_model = get_user_model()
        login_id = _ascii_digits(str(username).strip())
        submitted = str(password)
        cleaned = _ascii_digits(submitted.strip())
        matched_by = ''

        user = user_model.objects.filter(username=login_id).first()
        if user is not None:
            matched_by = 'username'

        if user is None:
            user = (
                user_model.objects.filter(profile__phone=login_id)
                .select_related('profile')
                .first()
            )
            if user is not None:
                matched_by = 'phone'

        if user is None:
            name_qs = (
                user_model.objects.filter(
                    Q(first_name=login_id) | Q(profile__display_name=login_id)
                )
                .select_related('profile')
                .distinct()
            )
            if name_qs.count() == 1:
                user = name_qs.first()
                matched_by = 'name'

        if user is None:
            # تشغيل تجزئة وهمية حتى لا يكشف زمن الرد وجود الحساب من عدمه
            user_model().set_password(cleaned)
            return None

        # كلمة السر المحفوظة وحدها — لا كلمات ثابتة داخل الكود
        if user.check_password(cleaned) and self.user_can_authenticate(user):
            return user
        return None
