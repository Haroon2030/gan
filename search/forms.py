"""نماذج إدارة المستخدمين."""

from __future__ import annotations

import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q

from .models import UserProfile
from .nav_permissions import (
    ROLE_CHOICES,
    ROLE_PURCHASE_MANAGER,
    ROLE_SUPER_ADMIN,
    STAFF_ROLE_NAMES,
    assignable_roles,
    ensure_user_nav_permission,
    preset_sections_for_role,
    role_flags,
    user_role_name,
)

User = get_user_model()
_PHONE_RE = re.compile(r'^[0-9+\-\s]{7,20}$')


class AppUserForm(forms.Form):
    name = forms.CharField(
        label='الاسم',
        max_length=150,
        widget=forms.TextInput(
            attrs={
                'placeholder': 'الاسم للدخول أيضاً',
                'autocomplete': 'off',
            }
        ),
    )
    phone = forms.CharField(
        label='الرقم',
        max_length=20,
        widget=forms.TextInput(
            attrs={
                'placeholder': '05xxxxxxxx',
                'autocomplete': 'off',
                'inputmode': 'tel',
                'dir': 'ltr',
            }
        ),
    )
    # يُعرض كبطاقات اختيار (RadioSelect) تشرح ما يفتحه كل دور
    role_name = forms.ChoiceField(
        label='الدور',
        choices=ROLE_CHOICES,
        initial=ROLE_PURCHASE_MANAGER,
        widget=forms.RadioSelect,
    )
    is_active = forms.BooleanField(
        label='الحساب مفعّل',
        required=False,
        initial=True,
        help_text='الحساب الموقوف لا يستطيع الدخول ويبقى في القائمة.',
    )
    password = forms.CharField(
        label='كلمة السر',
        required=False,
        widget=forms.PasswordInput(
            attrs={
                'placeholder': '••••••••',
                'autocomplete': 'new-password',
                'dir': 'ltr',
            },
            render_value=False,
        ),
    )

    def __init__(self, *args, instance: User | None = None, actor: User | None = None, **kwargs):
        self.instance = instance
        self.actor = actor
        super().__init__(*args, **kwargs)
        # اسمح بقيمة دور قديمة غير موجودة في القائمة
        current = ''
        if instance is not None:
            current = user_role_name(instance)
        choices = list(assignable_roles(actor) if actor is not None else ROLE_CHOICES)
        if current and current not in {c[0] for c in choices}:
            choices.append((current, f'{current} (دور سابق)'))
        self.fields['role_name'].choices = choices
        if instance is None:
            self.fields['password'].required = True
            self.fields['password'].widget.attrs['placeholder'] = 'كلمة سر (6 أحرف على الأقل)'
        else:
            self.fields['password'].help_text = 'اتركها فارغة إن لم ترد تغييرها'
            profile = getattr(instance, 'profile', None)
            self.fields['name'].initial = (
                (profile.display_name if profile else '')
                or instance.first_name
                or instance.username
                or ''
            ).strip()
            self.fields['phone'].initial = (
                (profile.phone if profile else '') or ''
            ).strip()
            self.fields['role_name'].initial = current or ROLE_PURCHASE_MANAGER
            self.fields['is_active'].initial = instance.is_active

    def clean_name(self) -> str:
        name = (self.cleaned_data.get('name') or '').strip()
        if not name:
            raise forms.ValidationError('الاسم مطلوب.')
        from .validators import contains_sql_injection

        if contains_sql_injection(name):
            raise forms.ValidationError('الاسم يحتوي رموزاً غير مسموحة.')
        # الاسم يُستخدم للدخول — امنع التكرار مع أسماء أو أرقام مستخدمين آخرين
        qs = User.objects.filter(
            Q(first_name=name)
            | Q(profile__display_name=name)
            | Q(username=name)
            | Q(profile__phone=name)
        ).distinct()
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('هذا الاسم مستخدم مسبقاً.')
        return name

    def clean_phone(self) -> str:
        phone = re.sub(r'\s+', '', (self.cleaned_data.get('phone') or '').strip())
        if not _PHONE_RE.match(phone):
            raise forms.ValidationError('الرقم غير صالح.')
        qs = UserProfile.objects.filter(phone=phone)
        if self.instance is not None:
            qs = qs.exclude(user_id=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('هذا الرقم مستخدم مسبقاً.')
        # لا تتعارض مع username لحساب آخر
        other = User.objects.filter(username=phone)
        if self.instance is not None:
            other = other.exclude(pk=self.instance.pk)
        if other.exists():
            raise forms.ValidationError('هذا الرقم مستخدم كمُعرّف دخول مسبقاً.')
        return phone

    def clean_role_name(self) -> str:
        role = (self.cleaned_data.get('role_name') or '').strip()
        if not role:
            raise forms.ValidationError('اختر الدور.')
        from .validators import contains_sql_injection

        if contains_sql_injection(role):
            raise forms.ValidationError('اسم الدور يحتوي رموزاً غير مسموحة.')
        return role[:100]

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get('role_name') or ''
        active = cleaned.get('is_active')
        if role == ROLE_SUPER_ADMIN and not getattr(self.actor, 'is_superuser', False) and (
            self.instance is None or not self.instance.is_superuser
        ):
            self.add_error('role_name', 'منح دور السوبر أدمن متاح للسوبر أدمن فقط.')
        target = self.instance
        if target is not None and self.actor is not None and target.pk == self.actor.pk:
            # يمنع المستخدم من قفل نفسه خارج الإدارة
            if not active:
                self.add_error('is_active', 'لا يمكنك إيقاف حسابك.')
            if role and role not in STAFF_ROLE_NAMES:
                self.add_error('role_name', 'لا يمكنك إزالة صلاحية الإدارة عن حسابك.')
        if target is not None and target.is_superuser and (role != ROLE_SUPER_ADMIN or not active):
            others = User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target.pk)
            if not others.exists():
                self.add_error('role_name', 'هذا آخر سوبر أدمن مفعّل؛ أضف سوبر أدمن آخر أولاً.')
        return cleaned

    def clean_password(self) -> str:
        password = (self.cleaned_data.get('password') or '').strip()
        if not password:
            if self.instance is None:
                raise forms.ValidationError('كلمة السر مطلوبة.')
            return ''
        if len(password) < 6:
            raise forms.ValidationError('كلمة السر يجب ألا تقل عن 6 أحرف.')
        validate_password(password)
        return password

    def save(self) -> User:
        name = self.cleaned_data['name']
        phone = self.cleaned_data['phone']
        role_name = self.cleaned_data.get('role_name') or ''
        password = self.cleaned_data.get('password') or ''
        is_active = bool(self.cleaned_data.get('is_active'))
        is_staff, is_superuser = role_flags(role_name)

        if self.instance is None:
            user = User.objects.create_user(
                username=phone,
                password=password,
                first_name=name,
                is_active=is_active,
                is_staff=is_staff,
                is_superuser=is_superuser,
            )
            action = 'created'
        else:
            user = self.instance
            user.username = phone
            user.first_name = name
            user.is_active = is_active
            user.is_staff = is_staff
            user.is_superuser = is_superuser
            if password:
                user.set_password(password)
            user.save()
            action = 'updated'

        profile, _ = UserProfile.objects.update_or_create(
            user=user,
            defaults={
                'phone': phone,
                'display_name': name,
                'role_name': role_name,
            },
        )

        if not user.is_staff:
            preset = preset_sections_for_role(role_name)
            if action == 'created':
                ensure_user_nav_permission(
                    user,
                    sections=list(preset) if preset is not None else [],
                )
            elif preset is not None:
                row = ensure_user_nav_permission(user, sections=list(preset))
                row.sections = list(preset)
                row.blocked_screens = []
                row.save(update_fields=['sections', 'blocked_screens', 'updated_at'])
                if hasattr(user, '_nav_permission_cache'):
                    delattr(user, '_nav_permission_cache')

        return user


# الخادم واسم الخدمة يُدمجان داخل وصف DSN — تمنع الأقواس والمسافات حقن عناوين أخرى
_ORACLE_HOST_RE = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,253}[A-Za-z0-9])?$')
_ORACLE_SERVICE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.\-$#]{0,127}$')
_ORACLE_IDENT_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_$#]{0,127}$')
_WAREHOUSE_CODE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_\-]{0,19}$')
MAX_COMPARE_WAREHOUSES = 30


def _ltr_text(placeholder: str = '') -> forms.TextInput:
    return forms.TextInput(
        attrs={'dir': 'ltr', 'autocomplete': 'off', 'spellcheck': 'false', 'placeholder': placeholder}
    )


class OracleConnectionForm(forms.Form):
    enabled = forms.BooleanField(label='تفعيل القراءة من أوراكل', required=False)
    host = forms.CharField(label='عنوان الخادم', max_length=255, widget=_ltr_text('10.0.0.5'))
    port = forms.IntegerField(
        label='المنفذ',
        min_value=1,
        max_value=65535,
        initial=1521,
        widget=forms.NumberInput(attrs={'dir': 'ltr', 'inputmode': 'numeric'}),
    )
    service_name = forms.CharField(
        label='اسم الخدمة (Service Name)', max_length=128, required=False, widget=_ltr_text('ORCL')
    )
    sid = forms.CharField(label='SID', max_length=64, required=False, widget=_ltr_text())
    username = forms.CharField(label='المستخدم', max_length=128, widget=_ltr_text())
    password = forms.CharField(
        label='كلمة المرور',
        required=False,
        strip=False,
        max_length=256,
        widget=forms.PasswordInput(
            attrs={'dir': 'ltr', 'autocomplete': 'new-password'},
            render_value=False,
        ),
    )
    year = forms.CharField(label='السنة', required=False, max_length=4, widget=forms.Select())
    unit = forms.CharField(label='الوحدة المحاسبية', required=False, max_length=3, widget=forms.Select())
    schema = forms.CharField(
        label='مخطط بصيغة أخرى (اختياري)',
        max_length=64,
        required=False,
        widget=_ltr_text('يُستخدم فقط إن لم تُختر سنة ووحدة'),
    )
    onyx_base_url = forms.CharField(
        label='رابط خدمة أونكس (Web Service)',
        max_length=500,
        required=False,
        widget=_ltr_text('http://server:8090/Service/OnyxService.svc'),
    )
    # تُعرض كقائمة/مربعات من مخازن القاعدة بعد «اتصال وجلب»؛ الحقل النصي للإدخال اليدوي فقط
    default_warehouse = forms.CharField(
        label='المخزن الافتراضي', max_length=20, required=False, widget=_ltr_text('رمز المخزن')
    )
    compare_warehouses = forms.CharField(
        label='مخازن المقارنة', max_length=500, required=False, widget=_ltr_text('رموز مفصولة بفاصلة')
    )
    # مخططات السنة/الوحدة المكتشفة من القاعدة — لإعادة عرض القوائم فقط، لا تُعتمد في الحفظ
    known_schemas = forms.CharField(required=False, max_length=4000, widget=forms.HiddenInput())

    def __init__(self, *args, has_saved_password: bool = False, require_schema: bool = True, **kwargs):
        self.has_saved_password = has_saved_password
        self.require_schema = require_schema
        super().__init__(*args, **kwargs)
        self.fields['password'].widget.attrs['placeholder'] = (
            'اتركه فارغاً للإبقاء على الحالية' if has_saved_password else ''
        )
        self.schema_options = self._schema_options()
        source = self.data if self.is_bound else self.initial
        current_year = str(source.get('year') or '').strip()
        current_unit = str(source.get('unit') or '').strip()
        years = {str(opt['year']) for opt in self.schema_options}
        units = {opt['unit'] for opt in self.schema_options}
        if current_year.isdigit() and len(current_year) == 4:
            years.add(current_year)
        if current_unit.isdigit() and len(current_unit) <= 3:
            units.add(current_unit)
        self.fields['year'].widget.choices = [('', '— اختر السنة —')] + [
            (y, y) for y in sorted(years, reverse=True)
        ]
        self.fields['unit'].widget.choices = [('', '— اختر الوحدة —')] + [
            (u, f'الوحدة {u}') for u in sorted(units, key=lambda u: (int(u), u))
        ]

    def _schema_options(self) -> list[dict]:
        from .oracle_connection import parse_year_schema

        raw = self.data.get('known_schemas') if self.is_bound else self.initial.get('known_schemas')
        options: dict[str, dict] = {}
        for name in str(raw or '').split(','):
            parsed = parse_year_schema(name)
            if parsed:
                schema = name.strip().upper()
                options[schema] = {'schema': schema, 'year': parsed[0], 'unit': parsed[1]}
        return list(options.values())

    def clean_year(self):
        from .oracle_connection import MAX_YEAR, MIN_YEAR

        value = self.cleaned_data['year'].strip()
        if value and not (value.isdigit() and len(value) == 4 and MIN_YEAR <= int(value) <= MAX_YEAR):
            raise forms.ValidationError('سنة غير صالحة.')
        return value

    def clean_unit(self):
        value = self.cleaned_data['unit'].strip()
        if value and not (value.isdigit() and 1 <= len(value) <= 3):
            raise forms.ValidationError('رقم الوحدة المحاسبية غير صالح.')
        return value

    def clean_host(self):
        host = self.cleaned_data['host'].strip()
        if not _ORACLE_HOST_RE.match(host):
            raise forms.ValidationError('عنوان الخادم غير صالح: اسم نطاق أو IP بدون مسافات أو رموز.')
        return host

    def clean_service_name(self):
        value = self.cleaned_data['service_name'].strip()
        if value and not _ORACLE_SERVICE_RE.match(value):
            raise forms.ValidationError('اسم الخدمة يحتوي رموزاً غير مسموحة.')
        return value

    def clean_sid(self):
        value = self.cleaned_data['sid'].strip()
        if value and not _ORACLE_SERVICE_RE.match(value):
            raise forms.ValidationError('SID يحتوي رموزاً غير مسموحة.')
        return value

    def clean_username(self):
        value = self.cleaned_data['username'].strip()
        if not _ORACLE_IDENT_RE.match(value):
            raise forms.ValidationError('اسم المستخدم غير صالح.')
        return value

    def clean_onyx_base_url(self):
        from urllib.parse import urlparse

        value = self.cleaned_data['onyx_base_url'].strip().rstrip('/')
        if not value:
            return ''
        error = forms.ValidationError(
            'رابط غير صالح. الصيغة: http://العنوان:المنفذ/Service/OnyxService.svc'
        )
        try:
            parsed = urlparse(value)
            port = parsed.port
        except ValueError:
            raise error from None
        host = parsed.hostname or ''
        if (
            parsed.scheme not in ('http', 'https')
            or not _ORACLE_HOST_RE.match(host)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or '..' in parsed.path
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise error
        return value

    def clean_default_warehouse(self):
        value = self.cleaned_data['default_warehouse'].strip()
        if value and not _WAREHOUSE_CODE_RE.match(value):
            raise forms.ValidationError('رمز المخزن غير صالح: أرقام أو حروف إنجليزية فقط.')
        return value

    def clean_compare_warehouses(self):
        from .oracle_connection import split_warehouses

        codes = split_warehouses(self.cleaned_data['compare_warehouses'])
        bad = [c for c in codes if not _WAREHOUSE_CODE_RE.match(c)]
        if bad:
            raise forms.ValidationError(f'رمز مخزن غير صالح: {bad[0]} — افصل الرموز بفاصلة.')
        if len(codes) > MAX_COMPARE_WAREHOUSES:
            raise forms.ValidationError(f'الحد الأقصى {MAX_COMPARE_WAREHOUSES} مخزناً للمقارنة.')
        return ','.join(codes)

    def clean_schema(self):
        value = self.cleaned_data['schema'].strip()
        if value and not _ORACLE_IDENT_RE.match(value):
            raise forms.ValidationError('اسم المخطط غير صالح: حرف إنجليزي ثم حروف/أرقام/_ فقط.')
        return value

    def clean(self):
        from .oracle_connection import year_schema_name

        cleaned = super().clean()
        if not cleaned.get('service_name') and not cleaned.get('sid'):
            self.add_error('service_name', 'أدخل اسم الخدمة أو SID.')
        if not cleaned.get('password') and not self.has_saved_password:
            self.add_error('password', 'كلمة المرور مطلوبة.')

        year, unit = cleaned.get('year'), cleaned.get('unit')
        if year and unit:
            cleaned['schema'] = year_schema_name(int(year), unit)
        elif (year or unit) and self.require_schema:
            self.add_error('year' if not year else 'unit', 'اختر السنة والوحدة المحاسبية معاً.')
        elif self.require_schema and not cleaned.get('schema') and 'schema' not in self.errors:
            self.add_error('year', 'اختر السنة والوحدة المحاسبية (أو أدخل اسم المخطط يدوياً).')
        return cleaned
