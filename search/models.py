from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class UserProfile(models.Model):
    """بيانات إضافية لمستخدم التطبيق (الاسم المعروض والرقم)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    display_name = models.CharField('الاسم', max_length=150)
    phone = models.CharField('الرقم', max_length=20, unique=True, db_index=True)
    role_name = models.CharField('اسم الدور', max_length=100, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'ملف مستخدم'
        verbose_name_plural = 'ملفات المستخدمين'

    def __str__(self) -> str:
        return f'{self.display_name} ({self.phone})'


class UserActivitySession(models.Model):
    """جلسة دخول: وقت الدخول ووقت الخروج لكل مستخدم."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_sessions',
        verbose_name='المستخدم',
    )
    user_name = models.CharField('الاسم', max_length=150)
    user_phone = models.CharField('الرقم', max_length=20, blank=True, default='')
    login_at = models.DateTimeField('وقت الدخول', db_index=True)
    logout_at = models.DateTimeField('وقت الخروج', null=True, blank=True, db_index=True)
    ip_address = models.CharField('عنوان IP', max_length=45, blank=True, default='')
    user_agent = models.CharField('المتصفح', max_length=300, blank=True, default='')
    session_key = models.CharField('مفتاح الجلسة', max_length=40, blank=True, default='', db_index=True)
    source = models.CharField('المصدر', max_length=16, default='web')

    class Meta:
        verbose_name = 'جلسة مستخدم'
        verbose_name_plural = 'جلسات المستخدمين'
        ordering = ['-login_at']
        indexes = [
            models.Index(fields=['user', 'login_at'], name='search_uact_user_login_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.user_name} @ {self.login_at}'


class UserScreenView(models.Model):
    """شاشة فتحها المستخدم أثناء جلسته (للتتبع في شاشة النشاط)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='screen_views',
        verbose_name='المستخدم',
    )
    session_key = models.CharField('مفتاح الجلسة', max_length=40, blank=True, default='', db_index=True)
    screen_key = models.CharField('مفتاح الشاشة', max_length=64)
    screen_label = models.CharField('الشاشة', max_length=120)
    viewed_at = models.DateTimeField('وقت الفتح', db_index=True)

    class Meta:
        verbose_name = 'فتح شاشة'
        verbose_name_plural = 'شاشات مفتوحة'
        ordering = ['-viewed_at']

    def __str__(self) -> str:
        return f'{self.screen_label} @ {self.viewed_at}'


class UserNavPermission(models.Model):
    """صلاحيات أقسام الشريط: أقسام ممنوحة + شاشات فرعية محجوبة."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='nav_permission',
        verbose_name='المستخدم',
    )
    sections = models.JSONField('الأقسام الممنوحة', default=list, blank=True)
    blocked_screens = models.JSONField('الشاشات المحجوبة', default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'صلاحية أقسام'
        verbose_name_plural = 'صلاحيات الأقسام'

    def __str__(self) -> str:
        return f'صلاحيات {self.user_id}'


class ItemBarcode(models.Model):
    """ربط الباركود برقم الصنف من GetAllItems."""

    barcode = models.CharField('الباركود', max_length=128, db_index=True)
    item_code = models.CharField('رقم الصنف', max_length=64, db_index=True)
    name = models.CharField('اسم الصنف', max_length=255, blank=True, default='')
    unit = models.CharField('الوحدة', max_length=64, blank=True, default='')
    pack_size = models.CharField('حجم العبوة', max_length=32, blank=True, default='')
    g_code = models.CharField('رمز المجموعة', max_length=64, blank=True, default='', db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'باركود صنف'
        verbose_name_plural = 'باركودات الأصناف'
        # بدون قيد فريد: نحتفظ بصفوف المصدر كما هي حتى عند تكرار شكل الوحدة.

    def __str__(self) -> str:
        return f'{self.barcode} → {self.item_code}'


class ItemGroup(models.Model):
    """مجموعات الأصناف من GetAllGroupDet (G_CODE)."""

    g_code = models.CharField('رمز المجموعة', max_length=64, unique=True)
    g_name = models.CharField('اسم المجموعة', max_length=255, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'مجموعة صنف'
        verbose_name_plural = 'مجموعات الأصناف'

    def __str__(self) -> str:
        return f'{self.g_code} — {self.g_name}'


class OracleConnection(models.Model):
    """ربط أوراكل المُدخل من شاشة الربط — صف واحد يتقدّم على قيم .env عند وجوده."""

    # صف واحد دائماً (pk=1) — يُفرض من الكود؛ MySQL يمنع CHECK على عمود AUTO_INCREMENT
    SINGLETON_PK = 1

    enabled = models.BooleanField('مفعّل', default=True)
    host = models.CharField('الخادم', max_length=255)
    port = models.PositiveIntegerField(
        'المنفذ',
        default=1521,
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
    )
    service_name = models.CharField('اسم الخدمة', max_length=128, blank=True, default='')
    sid = models.CharField('SID', max_length=64, blank=True, default='')
    username = models.CharField('المستخدم', max_length=128)
    password_encrypted = models.TextField('كلمة المرور (مشفّرة)', blank=True, default='')
    schema = models.CharField('المخطط', max_length=64)
    # فارغ = خدمة أونكس غير مضبوطة (تُكتشف تلقائياً من عنوان أوراكل في الشاشة)
    onyx_base_url = models.CharField('رابط خدمة أونكس', max_length=500, blank=True, default='')
    # تُختار من قائمة مخازن أوراكل في الشاشة؛ مقارنة فارغة = المخزن الافتراضي وحده
    default_warehouse = models.CharField('المخزن الافتراضي', max_length=20, blank=True, default='')
    compare_warehouses = models.CharField(
        'مخازن المقارنة', max_length=500, blank=True, default=''
    )
    # يتغيّر فقط عند تغيّر هوية مصدر البيانات — يعزل كاش العميل السابق
    data_version = models.PositiveIntegerField('إصدار البيانات', default=0)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='آخر تعديل بواسطة',
    )

    class Meta:
        verbose_name = 'ربط أوراكل'
        verbose_name_plural = 'ربط أوراكل'
        constraints = [
            models.CheckConstraint(
                condition=~(models.Q(service_name='') & models.Q(sid='')),
                name='search_oracleconn_service_or_sid',
            ),
        ]

    def __str__(self) -> str:
        target = self.service_name or self.sid
        return f'{self.username}@{self.host}:{self.port}/{target}'


class OracleLinkKeys(models.Model):
    """مفاتيح وكيل الربط — في القاعدة لأن مجلد البيانات داخل الحاوية يُمسح مع كل نشر.

    تُكتب للقرص عند كل إقلاع، ولا تتغيّر إلا بـ«توليد ملف ربط جديد» أو «إلغاء الربط».
    """

    SINGLETON_PK = 1

    host_private_key = models.TextField('مفتاح الخادم الخاص', blank=True, default='')
    host_public_key = models.TextField('مفتاح الخادم العام', blank=True, default='')
    # سطر authorized_keys المقيّد لجهاز العميل — فارغ = لا ربط
    authorized_keys = models.TextField('مفتاح جهاز العميل', blank=True, default='')
    agent_generated_at = models.DateTimeField('توليد مفتاح العميل', null=True, blank=True)
    # مفتاح المطوّر: يفتح النفق من جهاز التطوير إلى أوراكل (-L) — لا يعكس منفذاً ولا ينفّذ أوامر
    dev_authorized_keys = models.TextField('مفتاح المطوّر', blank=True, default='')
    dev_generated_at = models.DateTimeField('توليد مفتاح المطوّر', null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'مفاتيح وكيل الربط'
        verbose_name_plural = 'مفاتيح وكيل الربط'

    def __str__(self) -> str:
        return 'مفاتيح وكيل الربط'
