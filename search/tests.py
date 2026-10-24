from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from django.conf import settings

from search.models import UserActivitySession, UserNavPermission, UserProfile
from search.validators import contains_sql_injection

# استعلامات أوراكل تتطلب مخططاً؛ الاختبارات لا تعتمد على قيم .env المحلية
_with_test_schema = override_settings(ORACLE={**settings.ORACLE, 'SCHEMA': 'IAS20261'})


class SqlInjectionProtectionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='tester',
            password='StrongPassword123!',
        )
        self.client.force_login(self.user)

    def test_detects_classic_sqli_payloads(self):
        self.assertTrue(contains_sql_injection("' OR '1'='1"))
        self.assertTrue(contains_sql_injection('1; DROP TABLE users--'))
        self.assertTrue(contains_sql_injection('x UNION SELECT password FROM auth_user'))
        self.assertFalse(contains_sql_injection('06100'))
        self.assertFalse(contains_sql_injection('202478604'))


class AuthenticationTests(TestCase):
    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse('home'))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('home')}",
            fetch_redirect_response=False,
        )

    def test_home_shows_role_name_after_login(self):
        user = get_user_model().objects.create_user(
            username='0505555555',
            password='StrongPassword123!',
            first_name='سارة',
            is_staff=True,
        )
        UserProfile.objects.create(
            user=user,
            display_name='سارة',
            phone='0505555555',
            role_name='مدير مبيعات',
        )
        self.client.force_login(user)
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'سارة')
        self.assertContains(response, 'مدير مبيعات')
        self.assertContains(response, 'لوحة المؤشرات التنفيذية')

    def test_home_shows_role_cards_for_sales_manager(self):
        user = get_user_model().objects.create_user(
            username='0505666777',
            password='StrongPassword123!',
            first_name='نورة',
            is_staff=False,
        )
        UserProfile.objects.create(
            user=user,
            display_name='نورة',
            phone='0505666777',
            role_name='مدير مبيعات',
        )
        self.client.force_login(user)
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-hx-card="sales"')
        self.assertContains(response, 'المبيعات')
        # الربحية في القسم المالي: لا تظهر لمدير المبيعات
        self.assertNotContains(response, 'href="/profitability/"')
        self.assertNotContains(response, 'data-hx-card="purchases"')
        self.assertNotContains(response, 'حد ربح التسعير')


    @patch.dict(
        'os.environ',
        {
            'APP_LOGIN_USERNAME': 'production-user',
            'APP_LOGIN_PASSWORD': 'StrongProductionPassword123!',
        },
    )
    def test_ensure_app_user_creates_login_account(self):
        call_command('ensure_app_user')

        user = get_user_model().objects.get(username='production-user')
        self.assertTrue(user.check_password('StrongProductionPassword123!'))
        self.assertTrue(user.is_staff)
        self.assertTrue(UserProfile.objects.filter(user=user).exists())

    def test_bootstrap_runs_only_when_no_users_exist(self):
        env = {'APP_LOGIN_USERNAME': 'admin', 'APP_LOGIN_PASSWORD': 'OldPassword123!'}
        with patch.dict('os.environ', env):
            call_command('ensure_app_user')
        user = get_user_model().objects.get(username='admin')
        self.assertTrue(user.is_superuser)

        # بعد التغيير من شاشة المستخدمين لا يعيد الأمر كلمة السر ولا الصلاحيات
        user.set_password('ChangedInUi123!')
        user.is_active = False
        user.is_superuser = False
        user.save()
        with patch.dict('os.environ', env):
            call_command('ensure_app_user')
        user.refresh_from_db()
        self.assertTrue(user.check_password('ChangedInUi123!'))
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_superuser)

    def test_bootstrap_skips_when_other_users_exist(self):
        get_user_model().objects.create_user(username='0501111111', password='StaffPass123!')
        with patch.dict(
            'os.environ',
            {'APP_LOGIN_USERNAME': 'admin', 'APP_LOGIN_PASSWORD': 'TestPass123!'},
        ):
            call_command('ensure_app_user')
        self.assertFalse(get_user_model().objects.filter(username='admin').exists())

    def test_login_accepts_phone_number(self):
        user = get_user_model().objects.create_user(
            username='0509999999',
            password='PhoneLogin123!',
            first_name='موظف',
        )
        UserProfile.objects.create(user=user, display_name='موظف', phone='0509999999')

        ok = self.client.login(username='0509999999', password='PhoneLogin123!')
        self.assertTrue(ok)

    def test_login_accepts_display_name(self):
        user = get_user_model().objects.create_user(
            username='0508888888',
            password='NameLogin123!',
            first_name='سارة',
        )
        UserProfile.objects.create(user=user, display_name='سارة', phone='0508888888')

        ok = self.client.login(username='سارة', password='NameLogin123!')
        self.assertTrue(ok)

    def test_hardcoded_admin_passwords_no_longer_work(self):
        """الدخول الثابت (1 / admin بكلمة 526400 أو 1) أُزيل: كلمة السر الحقيقية وحدها تفتح الحساب."""
        for username in ('1', 'admin'):
            get_user_model().objects.create_user(
                username=username, password='Real-Pass-2026', is_staff=True, is_superuser=True,
            )
        for username in ('1', 'admin', '526400'):
            for password in ('526400', '1'):
                self.assertFalse(
                    self.client.login(username=username, password=password),
                    f'{username} / {password}',
                )
        self.assertTrue(self.client.login(username='1', password='Real-Pass-2026'))


class SalesDailyTableTests(TestCase):
    """جدول المبيعات اليومية: يوم لكل صف، صافي بعد المرتجع، الأحدث أولاً."""

    # كاش الملفات يبقى بين تشغيلات الاختبار — نتجاوزه حتى يُستدعى الاستعلام فعلاً
    @patch('search.oracle_stock._sales_cache_set')
    @patch('search.oracle_stock._sales_cache_get', return_value=None)
    @patch('search.oracle_stock._pos_owner', return_value='POS')
    @patch('search.oracle_stock.oracle_enabled', return_value=True)
    @patch('search.oracle_stock._fetch_all')
    def test_daily_rows_fill_missing_days_and_net_returns(self, fetch_all, _enabled, _owner, _get, _set):
        from datetime import date, datetime

        from search.oracle_stock import fetch_pos_daily_totals

        fetch_all.side_effect = [
            [
                {'DAY': datetime(2026, 10, 1), 'INVOICE_COUNT': 10, 'GROSS_TOTAL': 1000},
                {'DAY': datetime(2026, 10, 3), 'INVOICE_COUNT': 4, 'GROSS_TOTAL': 400},
            ],
            [{'DAY': datetime(2026, 10, 3), 'RET_COUNT': 1, 'RET_TOTAL': 50}],
        ]
        rows = fetch_pos_daily_totals(date(2026, 10, 1), date(2026, 10, 3), branch_code='7')
        self.assertEqual([r['date'] for r in rows], ['2026-10-01', '2026-10-02', '2026-10-03'])
        self.assertEqual(rows[1]['invoice_count'], 0)
        self.assertEqual(rows[2]['sales_total'], 350)
        self.assertEqual(rows[2]['return_total'], 50)
        sql, params = fetch_all.call_args_list[0][0]
        self.assertIn('TRUNC(p.BILL_DATE)', sql)
        self.assertIn('p.BRN_NO = :brn', sql)
        self.assertEqual(params['brn'], 7)

    def test_daily_range_starts_at_month_start(self):
        from datetime import date

        from search.sales_dashboard import daily_range

        self.assertEqual(daily_range(date(2026, 10, 22), date(2026, 10, 22)), (date(2026, 10, 1), date(2026, 10, 22)))
        self.assertEqual(daily_range(date(2026, 9, 15), date(2026, 10, 5)), (date(2026, 9, 15), date(2026, 10, 5)))
        self.assertEqual(daily_range(date(2026, 1, 1), date(2026, 10, 22))[0], date(2026, 7, 23))

    @patch('search.oracle_stock.oracle_session')
    @patch('search.oracle_stock.fetch_pos_daily_totals')
    def test_build_daily_newest_first_with_share(self, fetch, _session):
        from datetime import date

        from search.sales_dashboard import build_sales_daily

        fetch.return_value = [
            {'date': '2026-10-01', 'invoice_count': 3, 'return_count': 0, 'return_total': 0, 'sales_total': 300},
            {'date': '2026-10-02', 'invoice_count': 0, 'return_count': 0, 'return_total': 0, 'sales_total': 0},
            {'date': '2026-10-03', 'invoice_count': 1, 'return_count': 0, 'return_total': 0, 'sales_total': 100},
        ]
        daily = build_sales_daily(date(2026, 10, 3), date(2026, 10, 3))
        self.assertEqual(fetch.call_args[0][:2], (date(2026, 10, 1), date(2026, 10, 3)))
        self.assertEqual([r['date'] for r in daily['rows']], ['2026-10-03', '2026-10-02', '2026-10-01'])
        self.assertEqual(daily['rows'][0]['weekday'], 'السبت')
        self.assertTrue(daily['rows'][1]['no_sales'])
        self.assertEqual(daily['rows'][2]['share_display'], '75.0%')
        self.assertEqual(daily['totals']['sales_total_display'], '400.00')
        self.assertEqual(daily['totals']['selling_days'], 2)
        self.assertEqual(daily['totals']['avg_day_display'], '200.00')
        self.assertEqual(daily['best_day']['date'], '2026-10-01')
        self.assertEqual(daily['best_day']['weekday'], 'الخميس')
        self.assertEqual(daily['best_day']['sales_total_display'], '300.00')

    def test_return_calendar_weeks_levels_and_ranks(self):
        from search.sales_dashboard import _return_calendar

        # 2026-10-01 خميس → خمس خانات فارغة قبله (سبت..أربعاء)
        cal = _return_calendar([
            {'date': '2026-10-01', 'invoice_count': 5, 'return_count': 1, 'return_total': 10, 'sales_total': 90},
            {'date': '2026-10-02', 'invoice_count': 5, 'return_count': 0, 'return_total': 0, 'sales_total': 50},
            {'date': '2026-10-03', 'invoice_count': 5, 'return_count': 2, 'return_total': 40, 'sales_total': 160},
        ])
        first_week = cal['weeks'][0]
        self.assertEqual(len(first_week), 7)
        self.assertEqual(first_week[:5], [None] * 5)
        thu, fri = first_week[5], first_week[6]
        self.assertEqual((thu['day'], thu['level'], thu['rank'], thu['rate_display']), (1, 1, 2, '10.0%'))
        self.assertEqual((fri['level'], fri['rank']), (0, 0))
        sat = cal['weeks'][1][0]
        self.assertEqual((sat['day'], sat['level'], sat['rank'], sat['rate_display']), (3, 4, 1, '20.0%'))
        self.assertEqual(cal['weeks'][1][1:], [None] * 6)
        self.assertEqual(cal['count'], 2)
        self.assertEqual(cal['total_display'], '50.00')
        self.assertEqual(cal['top']['date'], '2026-10-03')
        self.assertEqual(_return_calendar([])['weeks'], [])

    @patch('search.oracle_stock.oracle_session')
    @patch('search.oracle_stock.fetch_pos_daily_totals')
    def test_best_day_is_none_without_sales(self, fetch, _session):
        from datetime import date

        from search.sales_dashboard import build_sales_daily

        fetch.return_value = [
            {'date': '2026-10-01', 'invoice_count': 0, 'return_count': 0, 'return_total': 0, 'sales_total': 0},
        ]
        self.assertIsNone(build_sales_daily(date(2026, 10, 1), date(2026, 10, 1))['best_day'])


class PosSalesTableTests(TestCase):
    @patch(
        'search.sales_dashboard._pos_store_branches',
        return_value={'6': 'سكاي مول'},
    )
    def test_pos_table_keeps_active_store(self, _stores):
        from datetime import date

        from search.sales_dashboard import _assemble_sales_branches_dashboard

        payload = _assemble_sales_branches_dashboard(
            [
                {
                    'branch_code': '6',
                    'branch_name': 'سكاي مول',
                    'invoice_count': 12,
                    'return_count': 0,
                    'return_total': 0,
                    'sales_total': 1500,
                    'avg_basket': 125,
                }
            ],
            [],
            [],
            date(2026, 8, 1),
            date(2026, 8, 16),
        )
        branches = payload['pos']['branches']
        self.assertEqual([row['branch_code'] for row in branches], ['6'])
        self.assertFalse(any(row.get('no_sales') for row in branches))

    @patch(
        'search.sales_dashboard._pos_store_branches',
        return_value={'6': 'سكاي مول', '18': 'فرع حائل', '7': 'فرع الدمام'},
    )
    def test_listed_pos_stores_without_sales_are_red_zeros(self, _stores):
        from datetime import date

        from search.sales_dashboard import _assemble_sales_branches_dashboard

        payload = _assemble_sales_branches_dashboard(
            [
                {
                    'branch_code': '6',
                    'branch_name': 'سكاي مول',
                    'invoice_count': 12,
                    'return_count': 0,
                    'return_total': 0,
                    'sales_total': 1500,
                    'avg_basket': 125,
                }
            ],
            [],
            [],
            date(2026, 8, 1),
            date(2026, 8, 16),
        )
        by_code = {row['branch_code']: row for row in payload['pos']['branches']}
        self.assertEqual(set(by_code), {'6', '18', '7'})
        self.assertFalse(by_code['6']['no_sales'])
        self.assertTrue(by_code['18']['no_sales'])
        self.assertTrue(by_code['7']['no_sales'])
        self.assertEqual(by_code['18']['sales_total_display'], '0.00')

    def test_pos_store_name_tokens(self):
        from search.sales_dashboard import _is_pos_store_name

        self.assertTrue(_is_pos_store_name('فرع الدمام'))
        self.assertTrue(_is_pos_store_name('سكاي مول'))
        self.assertTrue(_is_pos_store_name('فرع الربوة'))
        self.assertTrue(_is_pos_store_name('فرع الوااحة'))
        self.assertTrue(_is_pos_store_name('خميس مشيط'))
        self.assertFalse(_is_pos_store_name('الإدارة العامة'))
        self.assertFalse(_is_pos_store_name('فرع الثلاجة'))

    @patch(
        'search.sales_dashboard._pos_store_branches',
        return_value={'7': 'فرع الدمام', '6': 'سكاي مول'},
    )
    def test_dammam_sales_merge_float_branch_code(self, _stores):
        """مبيعات الدمام لا تُفصل عن صف الأصفار عند BRN=7.0 من أوراكل."""
        from datetime import date

        from search.sales_dashboard import _assemble_sales_branches_dashboard

        payload = _assemble_sales_branches_dashboard(
            [
                {
                    'branch_code': '7.0',
                    'branch_name': '7.0',
                    'invoice_count': 40,
                    'return_count': 1,
                    'return_total': 50,
                    'sales_total': 9000,
                    'avg_basket': 225,
                }
            ],
            [],
            [],
            date(2026, 8, 1),
            date(2026, 8, 16),
        )
        by_code = {row['branch_code']: row for row in payload['pos']['branches']}
        self.assertIn('7', by_code)
        self.assertNotIn('7.0', by_code)
        self.assertEqual(by_code['7']['branch_name'], 'فرع الدمام')
        self.assertFalse(by_code['7']['no_sales'])
        self.assertEqual(by_code['7']['sales_total'], 9000.0)


_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'income-tests'}}


_ENV_ORACLE = {
    'ENABLED': True,
    'HOST': 'env-host',
    'PORT': 1521,
    'SERVICE_NAME': 'ENVSVC',
    'SID': '',
    'USER': 'envuser',
    'PASSWORD': 'env-secret-pass-XYZ',
    'SCHEMA': 'ENVSCH',
    'CLIENT_LIB_DIR': '',
    'TCP_CONNECT_TIMEOUT': 30,
    'CONNECT_RETRY_COUNT': 1,
    'CONNECT_RETRY_DELAY': 1,
}

_ORACLE_CONN_CACHE = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'oracle-connection-tests',
        'KEY_FUNCTION': 'search.oracle_connection.cache_key',
    }
}


class OracleConnectionScreenTests(TestCase):
    def setUp(self):
        import tempfile

        from search import oracle_connection

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(
            ORACLE=dict(_ENV_ORACLE),
            ORACLE_CONNECTION_SECRET='test-connection-secret',
            ORACLE_CONNECTION_STAMP_FILE=f'{tmp.name}/oracle_connection.stamp',
            CACHES=_ORACLE_CONN_CACHE,
        )
        override.enable()
        self.addCleanup(override.disable)
        oracle_connection.invalidate()
        self.addCleanup(oracle_connection.invalidate)
        self.url = reverse('oracle_connection_settings')
        self.admin = get_user_model().objects.create_superuser(
            username='root-admin', password='StrongPassword123!', email='root@example.com'
        )

    def _post(self, action='save', **overrides):
        data = {
            'action': action,
            'enabled': 'on',
            'host': '10.0.0.5',
            'port': '1521',
            'service_name': 'CUSTSVC',
            'sid': '',
            'username': 'custuser',
            'password': 'cust-secret-pass-ABC',
            'schema': 'CUSTSCH',
        }
        data.update(overrides)
        data = {key: value for key, value in data.items() if value is not None}
        return self.client.post(self.url, data)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))

    def test_regular_user_is_redirected_home(self):
        user = get_user_model().objects.create_user(username='plain', password='StrongPassword123!')
        self.client.force_login(user)
        response = self.client.post(self.url, {'action': 'save'})
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)

    @patch('search.oracle_stock.probe_connection')
    def test_staff_without_superuser_gets_403_even_via_direct_post(self, probe):
        from search.models import OracleConnection

        staff = get_user_model().objects.create_user(
            username='staff-only', password='StrongPassword123!', is_staff=True
        )
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self._post().status_code, 403)
        probe.assert_not_called()
        self.assertFalse(OracleConnection.objects.exists())

    def test_superuser_sees_env_values_but_never_the_password(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'env-host')
        self.assertNotContains(response, _ENV_ORACLE['PASSWORD'])

    def test_sidebar_link_shown_only_to_superuser(self):
        staff = get_user_model().objects.create_user(
            username='staff-nav', password='StrongPassword123!', is_staff=True
        )
        self.client.force_login(staff)
        self.assertNotContains(self.client.get(reverse('home')), self.url)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('home')), self.url)

    @patch('search.oracle_stock.probe_connection')
    def test_save_stores_encrypted_password_and_switches_source(self, probe):
        from search.models import OracleConnection
        from search.oracle_connection import (
            connection_source,
            decrypt_secret,
            effective_oracle_config,
        )
        from search.oracle_stock import _cfg, oracle_enabled

        self.client.force_login(self.admin)
        response = self._post()
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        probe.assert_called_once()

        row = OracleConnection.objects.get()
        self.assertEqual(row.updated_by, self.admin)
        self.assertNotIn('cust-secret-pass-ABC', row.password_encrypted)
        self.assertEqual(decrypt_secret(row.password_encrypted), 'cust-secret-pass-ABC')

        cfg = effective_oracle_config()
        self.assertEqual(connection_source(), 'screen')
        self.assertEqual((cfg['HOST'], cfg['USER'], cfg['SCHEMA']), ('10.0.0.5', 'custuser', 'CUSTSCH'))
        self.assertEqual(cfg['PASSWORD'], 'cust-secret-pass-ABC')
        self.assertEqual(cfg['TCP_CONNECT_TIMEOUT'], 30)
        self.assertEqual(_cfg()['HOST'], '10.0.0.5')
        self.assertTrue(oracle_enabled())

        page = self.client.get(self.url)
        self.assertNotContains(page, 'cust-secret-pass-ABC')

    @patch('search.oracle_stock.probe_connection')
    def test_failed_probe_saves_nothing(self, probe):
        from search.models import OracleConnection
        from search.oracle_stock import OracleStockError

        probe.side_effect = OracleStockError('بيانات الدخول غير صحيحة')
        self.client.force_login(self.admin)
        response = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'لم يُحفظ الربط')
        self.assertFalse(OracleConnection.objects.exists())

    @patch('search.oracle_stock.probe_connection')
    def test_test_action_reports_result_without_saving(self, probe):
        from search.models import OracleConnection

        self.client.force_login(self.admin)
        response = self._post(action='test')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'نجح الاتصال')
        self.assertFalse(OracleConnection.objects.exists())

    def test_unknown_action_is_rejected(self):
        self.client.force_login(self.admin)
        self.assertEqual(self._post(action='drop').status_code, 400)

    @patch('search.oracle_stock.probe_connection')
    def test_dsn_injection_in_host_is_rejected_before_probe(self, probe):
        from search.models import OracleConnection

        self.client.force_login(self.admin)
        response = self._post(host='evil)(HOST=attacker')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('host'))
        probe.assert_not_called()
        self.assertFalse(OracleConnection.objects.exists())

    @patch('search.oracle_stock.probe_connection')
    def test_service_or_sid_is_required(self, probe):
        self.client.force_login(self.admin)
        response = self._post(service_name='', sid='')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('service_name'))
        probe.assert_not_called()

    @patch('search.oracle_stock.probe_connection')
    def test_blank_password_on_update_keeps_saved_password(self, probe):
        from search.models import OracleConnection
        from search.oracle_connection import decrypt_secret

        self.client.force_login(self.admin)
        self._post()
        response = self._post(port='1522', password='')
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        row = OracleConnection.objects.get()
        self.assertEqual(row.port, 1522)
        self.assertEqual(decrypt_secret(row.password_encrypted), 'cust-secret-pass-ABC')
        self.assertEqual(probe.call_args.args[0]['PASSWORD'], 'cust-secret-pass-ABC')

    @patch('search.oracle_stock.probe_connection')
    def test_disabled_save_skips_probe_and_turns_oracle_off(self, probe):
        from search.oracle_stock import oracle_enabled

        self.client.force_login(self.admin)
        response = self._post(enabled=None)
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        probe.assert_not_called()
        self.assertFalse(oracle_enabled())

    @patch('search.oracle_stock.probe_connection')
    def test_same_identity_as_env_keeps_warm_cache(self, probe):
        from django.core.cache import cache

        from search.models import OracleConnection

        cache.set('report-k', 'env-data')
        self.client.force_login(self.admin)
        self._post(
            host='env-host',
            service_name='ENVSVC',
            username='envuser',
            schema='ENVSCH',
            password='rotated-pass',
        )
        self.assertEqual(OracleConnection.objects.get().data_version, 0)
        self.assertEqual(cache.get('report-k'), 'env-data')

    @patch('search.oracle_stock.probe_connection')
    def test_new_data_source_isolates_cached_reports(self, probe):
        from django.core.cache import cache

        from search.models import OracleConnection
        from search.oracle_connection import cache_key

        self.assertEqual(cache_key('k', '', 1), ':1:k')
        cache.set('report-k', 'env-data')
        self.client.force_login(self.admin)
        self._post()
        self.assertEqual(OracleConnection.objects.get().data_version, 1)
        self.assertEqual(cache_key('k', '', 1), ':1:oc1:k')
        self.assertIsNone(cache.get('report-k'))

        cache.set('report-k', 'customer-data')
        self._post(password='')
        self.assertEqual(OracleConnection.objects.get().data_version, 1)
        self.assertEqual(cache.get('report-k'), 'customer-data')

        self._post(schema='OTHERSCH', password='')
        self.assertEqual(OracleConnection.objects.get().data_version, 2)
        self.assertIsNone(cache.get('report-k'))

    def test_password_encrypted_with_another_secret_is_unreadable(self):
        from search.oracle_connection import OracleConnectionError, decrypt_secret, encrypt_secret

        token = encrypt_secret('abc')
        with override_settings(ORACLE_CONNECTION_SECRET='different-secret'):
            with self.assertRaises(OracleConnectionError):
                decrypt_secret(token)

    @patch('search.oracle_stock.probe_connection')
    def test_year_and_unit_build_the_onyx_schema(self, probe):
        from search.models import OracleConnection

        self.client.force_login(self.admin)
        response = self._post(schema='', year='2025', unit='2')
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(probe.call_args.args[0]['SCHEMA'], 'IAS20252')
        self.assertEqual(OracleConnection.objects.get().schema, 'IAS20252')

        page = self.client.get(self.url)
        self.assertEqual(page.context['form'].initial['year'], '2025')
        self.assertEqual(page.context['form'].initial['unit'], '2')

    @patch('search.oracle_stock.probe_connection')
    def test_year_without_unit_is_rejected(self, probe):
        self.client.force_login(self.admin)
        response = self._post(schema='', year='2026', unit='')
        self.assertTrue(response.context['form'].errors.get('unit'))
        probe.assert_not_called()

    @patch('search.oracle_stock.probe_connection')
    def test_missing_year_unit_and_schema_is_rejected(self, probe):
        self.client.force_login(self.admin)
        response = self._post(schema='')
        self.assertTrue(response.context['form'].errors.get('year'))
        probe.assert_not_called()

    @patch('search.oracle_stock.probe_connection')
    def test_invalid_year_is_rejected(self, probe):
        self.client.force_login(self.admin)
        response = self._post(schema='', year='1999', unit='1')
        self.assertTrue(response.context['form'].errors.get('year'))
        probe.assert_not_called()

    def test_discover_lists_year_unit_schemas_without_saving(self):
        from search.models import OracleConnection

        self.client.force_login(self.admin)
        rows = [('IAS20261',), ('IAS20251',), ('IAS20262',), ('IASX',)]
        with (
            patch('search.oracle_stock._run_candidate_query', return_value=rows) as run,
            patch('search.oracle_stock.discover_warehouses', return_value=[]),
            patch('search.api_client.detect_onyx_service_url', return_value=''),
        ):
            response = self.client.post(
                self.url,
                {'action': 'discover', 'enabled': 'on', 'host': '10.0.0.5', 'port': '1521',
                 'service_name': 'CUSTSVC', 'username': 'custuser', 'password': 'pw-1'},
                HTTP_X_REQUESTED_WITH='fetch',
            )
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(
            [s['schema'] for s in payload['schemas']], ['IAS20261', 'IAS20262', 'IAS20251']
        )
        self.assertEqual(payload['schemas'][0], {'schema': 'IAS20261', 'year': 2026, 'unit': '1'})
        self.assertEqual(payload['selected'], {'schema': 'IAS20261', 'year': '2026', 'unit': '1'})
        self.assertEqual(run.call_args.args[0]['PASSWORD'], 'pw-1')
        self.assertFalse(OracleConnection.objects.exists())

    def _discover_fake_db(self, sql_log):
        def run(cfg, sql, **kwargs):
            sql_log.append(sql)
            if 'ALL_OBJECTS' in sql:
                return [('IAS20251',), ('IAS20261',)]
            if 'WAREHOUSE_DETAILS' in sql:
                return [('1201', 'مخزن الفرع', None, '2'), ('60', 'المخزن الرئيسي', None, '1'),
                        ('60', 'مكرر', None, '1')]
            if 'S_BRN' in sql:
                return [(1, 'فرع الرياض', None), (2, '- فرع  جدة', None)]
            raise AssertionError(sql)
        return run

    def test_discover_fetches_everything_from_credentials_only(self):
        from search.models import OracleConnection

        self.client.force_login(self.admin)
        sql_log: list[str] = []
        ok = type('R', (), {'status_code': 200})()
        with (
            patch('search.oracle_stock._run_candidate_query', side_effect=self._discover_fake_db(sql_log)),
            patch('search.api_client.requests.get', return_value=ok) as http_get,
        ):
            payload = self.client.post(
                self.url,
                {'action': 'discover', 'host': '10.0.0.5', 'port': '1521',
                 'service_name': 'CUSTSVC', 'username': 'custuser', 'password': 'pw-1'},
                HTTP_X_REQUESTED_WITH='fetch',
            ).json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['selected']['schema'], 'IAS20261')
        self.assertTrue(any('IAS20261.WAREHOUSE_DETAILS' in sql for sql in sql_log))
        self.assertEqual(payload['warehouses'], [
            {'code': '60', 'name': 'المخزن الرئيسي', 'branch_name': 'فرع الرياض'},
            {'code': '1201', 'name': 'مخزن الفرع', 'branch_name': 'فرع جدة'},
        ])
        self.assertEqual(payload['onyx_base_url'], 'http://10.0.0.5:8090/Service/OnyxService.svc')
        self.assertTrue(payload['onyx_detected'])
        self.assertEqual(http_get.call_args.args[0], 'http://10.0.0.5:8090/Service/OnyxService.svc')
        self.assertFalse(http_get.call_args.kwargs['allow_redirects'])
        self.assertFalse(OracleConnection.objects.exists())

    def test_discover_keeps_chosen_year_and_manual_onyx_url(self):
        self.client.force_login(self.admin)
        sql_log: list[str] = []
        with (
            patch('search.oracle_stock._run_candidate_query', side_effect=self._discover_fake_db(sql_log)),
            patch('search.api_client.detect_onyx_service_url') as detect,
        ):
            payload = self.client.post(
                self.url,
                {'action': 'discover', 'host': '10.0.0.5', 'port': '1521', 'service_name': 'CUSTSVC',
                 'username': 'custuser', 'password': 'pw-1', 'year': '2025', 'unit': '1',
                 'onyx_base_url': 'http://onyx.example.com:8090/Service/OnyxService.svc'},
                HTTP_X_REQUESTED_WITH='fetch',
            ).json()
        detect.assert_not_called()
        self.assertEqual(payload['selected']['schema'], 'IAS20251')
        self.assertTrue(any('IAS20251.WAREHOUSE_DETAILS' in sql for sql in sql_log))
        self.assertFalse(payload['onyx_detected'])
        self.assertEqual(payload['onyx_base_url'], 'http://onyx.example.com:8090/Service/OnyxService.svc')

    def test_discover_with_partial_year_picks_latest_instead_of_failing(self):
        self.client.force_login(self.admin)
        with (
            patch('search.oracle_stock._run_candidate_query', side_effect=self._discover_fake_db([])),
            patch('search.api_client.detect_onyx_service_url', return_value=''),
        ):
            payload = self.client.post(
                self.url,
                {'action': 'discover', 'host': '10.0.0.5', 'port': '1521', 'service_name': 'CUSTSVC',
                 'username': 'custuser', 'password': 'pw-1', 'year': '2025'},
                HTTP_X_REQUESTED_WITH='fetch',
            ).json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['selected']['schema'], 'IAS20261')

    def test_discover_reports_missing_warehouses_without_failing(self):
        from search.oracle_stock import OracleStockError

        self.client.force_login(self.admin)
        with (
            patch('search.oracle_stock.discover_year_schemas',
                  return_value=[{'schema': 'IAS20261', 'year': 2026, 'unit': '1'}]),
            patch('search.oracle_stock.discover_warehouses', side_effect=OracleStockError('لا مخازن')),
            patch('search.api_client.detect_onyx_service_url', return_value=''),
        ):
            payload = self.client.post(
                self.url,
                {'action': 'discover', 'host': '10.0.0.5', 'port': '1521', 'service_name': 'CUSTSVC',
                 'username': 'custuser', 'password': 'pw-1'},
                HTTP_X_REQUESTED_WITH='fetch',
            ).json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['warehouses'], [])
        self.assertEqual(payload['warehouse_error'], 'لا مخازن')
        self.assertIn('لا مخازن', payload['message'])

    def test_discover_reports_connection_error_as_json(self):
        from search.oracle_stock import OracleStockError

        self.client.force_login(self.admin)
        with (
            patch(
                'search.oracle_stock._run_candidate_query',
                side_effect=OracleStockError('اسم المستخدم أو كلمة المرور غير صحيحة.'),
            ),
            patch('search.api_client.detect_onyx_service_url') as detect,
        ):
            response = self.client.post(
                self.url,
                {'action': 'discover', 'host': '10.0.0.5', 'port': '1521',
                 'service_name': 'CUSTSVC', 'username': 'custuser', 'password': 'bad'},
                HTTP_X_REQUESTED_WITH='fetch',
            )
        payload = response.json()
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'اسم المستخدم أو كلمة المرور غير صحيحة.')
        self.assertEqual(payload['message'], 'اسم المستخدم أو كلمة المرور غير صحيحة.')
        self.assertEqual(payload['schemas'], [])
        detect.assert_not_called()

    def test_discover_requires_superuser(self):
        staff = get_user_model().objects.create_user(
            username='staff-disc', password='StrongPassword123!', is_staff=True
        )
        self.client.force_login(staff)
        with patch('search.oracle_stock._run_candidate_query') as run:
            response = self.client.post(self.url, {'action': 'discover'}, HTTP_X_REQUESTED_WITH='fetch')
        self.assertEqual(response.status_code, 403)
        run.assert_not_called()

    @patch('search.oracle_stock.probe_connection')
    def test_test_action_returns_json_for_fetch(self, probe):
        self.client.force_login(self.admin)
        response = self.client.post(
            self.url,
            {'action': 'test', 'host': '10.0.0.5', 'port': '1521', 'service_name': 'CUSTSVC',
             'username': 'custuser', 'password': 'pw', 'year': '2026', 'unit': '1'},
            HTTP_X_REQUESTED_WITH='fetch',
        )
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertIn('IAS20261', payload['message'])

    @patch('search.oracle_stock.probe_connection')
    def test_onyx_api_year_follows_saved_schema_year(self, probe):
        from search.api_client import _api_cfg

        with override_settings(EXTERNAL_API={
            'BASE_URL': '', 'ITEMS_PARAMS': {'year': 2026, 'active': 1},
            'EXTRA_PARAMS': {'year': 2026, 'active': 1, 'lev_no': '1'},
        }):
            self.assertEqual(_api_cfg()['EXTRA_PARAMS']['year'], 2026)
            self.client.force_login(self.admin)
            self._post(schema='', year='2024', unit='1')
            cfg = _api_cfg()
            self.assertEqual(cfg['EXTRA_PARAMS']['year'], 2024)
            self.assertEqual(cfg['ITEMS_PARAMS'], {'year': 2024, 'active': 1})
            self.assertEqual(cfg['EXTRA_PARAMS']['lev_no'], '1')
            from django.conf import settings as dj_settings
            self.assertEqual(dj_settings.EXTERNAL_API['EXTRA_PARAMS']['year'], 2026)

    @patch('search.oracle_stock.probe_connection')
    def test_manual_schema_keeps_env_onyx_year(self, probe):
        from search.oracle_connection import screen_onyx_year

        self.client.force_login(self.admin)
        self._post(schema='CUSTOMSCH')
        self.assertIsNone(screen_onyx_year())

    @patch('search.api_client.probe_onyx_service')
    @patch('search.oracle_stock.probe_connection')
    def test_onyx_url_saved_with_oracle_disabled(self, probe, onyx_probe):
        from search.api_client import _api_cfg
        from search.models import OracleConnection
        from search.oracle_stock import oracle_enabled

        url = 'http://SkyR.example.com:8090/Service/OnyxService.svc'
        with override_settings(EXTERNAL_API={'BASE_URL': 'http://env-host:8090/S', 'ALLOWED_HOSTS': ['env-host']}):
            self.client.force_login(self.admin)
            response = self._post(enabled=None, onyx_base_url=url + '/')
            self.assertRedirects(response, self.url, fetch_redirect_response=False)
            probe.assert_not_called()
            onyx_probe.assert_called_once_with(url)
            self.assertEqual(OracleConnection.objects.get().onyx_base_url, url)
            self.assertFalse(oracle_enabled())
            cfg = _api_cfg()
            self.assertEqual(cfg['BASE_URL'], url)
            self.assertEqual(cfg['ALLOWED_HOSTS'], ['env-host', 'skyr.example.com'])

    @patch('search.api_client.probe_onyx_service')
    @patch('search.oracle_stock.probe_connection')
    def test_onyx_probe_failure_blocks_save(self, probe, onyx_probe):
        from search.api_client import ApiClientError
        from search.models import OracleConnection

        onyx_probe.side_effect = ApiClientError('خدمة أونكس لم ترد')
        self.client.force_login(self.admin)
        response = self._post(onyx_base_url='http://10.0.0.9:8090/Service/OnyxService.svc')
        self.assertContains(response, 'فشل اختبار خدمة أونكس')
        self.assertFalse(OracleConnection.objects.exists())

    @patch('search.api_client.probe_onyx_service')
    @patch('search.oracle_stock.probe_connection')
    def test_unsafe_onyx_urls_are_rejected(self, probe, onyx_probe):
        self.client.force_login(self.admin)
        for bad in (
            'javascript:alert(1)',
            'ftp://host/x',
            'http://user:pw@host:8090/S',
            'http://host:8090/S?x=1',
            'http://ho st:8090/S',
            'http://host:99999/S',
        ):
            response = self._post(onyx_base_url=bad)
            self.assertTrue(response.context['form'].errors.get('onyx_base_url'), bad)
        onyx_probe.assert_not_called()

    @patch('search.api_client.probe_onyx_service')
    @patch('search.oracle_stock.probe_connection')
    def test_onyx_url_change_isolates_cache(self, probe, onyx_probe):
        from search.models import OracleConnection

        self.client.force_login(self.admin)
        self._post()
        self.assertEqual(OracleConnection.objects.get().data_version, 1)
        self._post(password='', onyx_base_url='http://10.0.0.9:8090/Service/OnyxService.svc')
        self.assertEqual(OracleConnection.objects.get().data_version, 2)

    @patch('search.api_client.probe_onyx_service')
    @patch('search.oracle_stock.probe_connection')
    def test_test_action_reports_oracle_and_onyx_separately(self, probe, onyx_probe):
        from search.oracle_stock import OracleStockError

        probe.side_effect = OracleStockError('انتهت مهلة الشبكة')
        self.client.force_login(self.admin)
        response = self.client.post(
            self.url,
            {'action': 'test', 'host': '10.0.0.5', 'port': '1521', 'service_name': 'S',
             'username': 'u', 'password': 'p', 'schema': 'IAS20261',
             'onyx_base_url': 'http://10.0.0.9:8090/Service/OnyxService.svc'},
            HTTP_X_REQUESTED_WITH='fetch',
        )
        payload = response.json()
        self.assertFalse(payload['ok'])
        self.assertIn('أوراكل: انتهت مهلة الشبكة', payload['message'])
        self.assertIn('خدمة أونكس: تستجيب', payload['message'])

    def _post_fetch(self, **overrides):
        data = {
            'action': 'save', 'enabled': 'on', 'host': '10.0.0.5', 'port': '1521',
            'service_name': 'CUSTSVC', 'username': 'custuser', 'password': 'pw-1',
            'year': '2026', 'unit': '1',
        }
        data.update(overrides)
        data = {k: v for k, v in data.items() if v is not None}
        return self.client.post(self.url, data, HTTP_X_REQUESTED_WITH='fetch')

    @patch('search.oracle_stock.probe_connection')
    def test_fetch_save_success_returns_redirect(self, probe):
        from search.models import OracleConnection

        self.client.force_login(self.admin)
        payload = self._post_fetch().json()
        self.assertEqual(payload, {'ok': True, 'redirect': self.url})
        self.assertEqual(OracleConnection.objects.get().schema, 'IAS20261')

    @patch('search.api_client.probe_onyx_service')
    @patch('search.oracle_stock.probe_connection')
    def test_fetch_save_oracle_failure_offers_disabled_save(self, probe, onyx_probe):
        from search.models import OracleConnection
        from search.oracle_stock import OracleStockError

        probe.side_effect = OracleStockError('انتهت مهلة الشبكة')
        self.client.force_login(self.admin)
        url = 'http://10.0.0.9:8090/Service/OnyxService.svc'
        payload = self._post_fetch(onyx_base_url=url).json()
        self.assertFalse(payload['ok'])
        self.assertTrue(payload['can_save_disabled'])
        self.assertIn('انتهت مهلة الشبكة', payload['message'])
        self.assertFalse(OracleConnection.objects.exists())

        probe.reset_mock()
        payload = self._post_fetch(enabled=None, onyx_base_url=url).json()
        self.assertTrue(payload['ok'])
        probe.assert_not_called()
        row = OracleConnection.objects.get()
        self.assertFalse(row.enabled)
        self.assertEqual(row.onyx_base_url, url)

    @patch('search.oracle_stock.probe_connection')
    def test_fetch_save_invalid_form_returns_json_error(self, probe):
        self.client.force_login(self.admin)
        payload = self._post_fetch(host='bad host').json()
        self.assertFalse(payload['ok'])
        self.assertIn('عنوان الخادم', payload['message'])
        probe.assert_not_called()

    @patch('search.oracle_stock.probe_connection')
    def test_warehouses_saved_from_screen_override_env(self, probe):
        from search.api_client import _api_cfg
        from search.models import OracleConnection

        env_api = {
            'BASE_URL': '', 'DEFAULT_WAREHOUSE': '1', 'COMPARE_WAREHOUSES': ['1'],
            'EXTRA_PARAMS': {'year': 2026, 'price_w_code': '1'},
        }
        with override_settings(EXTERNAL_API=env_api):
            self.client.force_login(self.admin)
            payload = self._post_fetch(
                default_warehouse='60', compare_warehouses='1201، 30 ,60,30'
            ).json()
            self.assertTrue(payload['ok'])
            row = OracleConnection.objects.get()
            self.assertEqual((row.default_warehouse, row.compare_warehouses), ('60', '1201,30,60'))
            cfg = _api_cfg()
            self.assertEqual(cfg['DEFAULT_WAREHOUSE'], '60')
            self.assertEqual(cfg['EXTRA_PARAMS']['price_w_code'], '60')
            self.assertEqual(cfg['COMPARE_WAREHOUSES'], ['1201', '30', '60'])
            self.assertEqual(row.data_version, 1)

            self._post_fetch(password='', default_warehouse='', compare_warehouses='')
            cfg = _api_cfg()
            self.assertEqual(cfg['DEFAULT_WAREHOUSE'], '1')
            self.assertEqual(cfg['COMPARE_WAREHOUSES'], ['1'])
            self.assertEqual(OracleConnection.objects.get().data_version, 1)

    @patch('search.oracle_stock.probe_connection')
    def test_invalid_warehouse_codes_are_rejected(self, probe):
        from search.models import OracleConnection

        self.client.force_login(self.admin)
        for field, bad in (
            ('default_warehouse', '6 0'),
            ('default_warehouse', '60/1'),
            ('compare_warehouses', '1, 3;0'),
            ('compare_warehouses', ','.join(str(i) for i in range(31))),
        ):
            payload = self._post_fetch(**{field: bad}).json()
            self.assertFalse(payload['ok'], bad)
        # أنماط حقن SQL يرفضها وسيط الحماية قبل وصولها للنموذج
        self.assertEqual(self._post_fetch(default_warehouse="60'--").status_code, 403)
        probe.assert_not_called()
        self.assertFalse(OracleConnection.objects.exists())

    @patch('search.oracle_stock.probe_connection')
    def test_stock_source_follows_screen_toggle(self, probe):
        from search.oracle_connection import stock_qty_source

        with override_settings(STOCK_QTY_SOURCE='api'):
            self.assertEqual(stock_qty_source(), 'api')
            self.client.force_login(self.admin)
            self._post_fetch()
            self.assertEqual(stock_qty_source(), 'oracle')
            self._post_fetch(enabled=None, password='')
            self.assertEqual(stock_qty_source(), 'api')

    def test_stock_source_without_screen_row_keeps_env(self):
        from search.oracle_connection import stock_qty_source

        with override_settings(STOCK_QTY_SOURCE='oracle'):
            self.assertEqual(stock_qty_source(), 'oracle')

    def test_parse_year_schema(self):
        from search.oracle_connection import parse_year_schema, year_schema_name

        self.assertEqual(parse_year_schema('IAS20261'), (2026, '1'))
        self.assertEqual(parse_year_schema('ias202612'), (2026, '12'))
        self.assertIsNone(parse_year_schema('IAS2026'))
        self.assertIsNone(parse_year_schema('IAS_YEAR_SCHEMA'))
        self.assertIsNone(parse_year_schema('IAS19991'))
        self.assertEqual(year_schema_name(2026, '1'), 'IAS20261')

    def test_without_saved_row_env_config_is_used_as_before(self):
        from search.oracle_connection import connection_source, effective_oracle_config

        self.assertEqual(connection_source(), 'env')
        self.assertEqual(effective_oracle_config(), _ENV_ORACLE)

    def test_opening_screen_never_waits_for_oracle(self):
        self.client.force_login(self.admin)
        with patch('search.oracle_stock._fetch_all') as fetch_all, patch('search.oracle_stock._connect') as connect:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        fetch_all.assert_not_called()
        connect.assert_not_called()

    @patch('search.oracle_stock.probe_connection')
    def test_default_warehouse_falls_back_to_first_compare_not_hardcoded(self, probe):
        from search.api_client import default_warehouse

        with override_settings(EXTERNAL_API={'BASE_URL': '', 'DEFAULT_WAREHOUSE': '', 'COMPARE_WAREHOUSES': []}):
            self.assertEqual(default_warehouse(), '')
            self.client.force_login(self.admin)
            self._post_fetch(default_warehouse='', compare_warehouses='701, 30')
            self.assertEqual(default_warehouse(), '701')
            self._post_fetch(password='', default_warehouse='30', compare_warehouses='')
            self.assertEqual(default_warehouse(), '30')


class SettingsCarryNoClientDataTests(TestCase):
    """النسخة المنقولة لعميل جديد لا تحمل بيانات عميل من .env — الشاشة هي المصدر الوحيد."""

    def test_client_values_are_not_read_from_environment(self):
        import importlib
        import os

        import config.settings as project_settings

        client_env = {
            'ORACLE_ENABLED': 'True', 'ORACLE_HOST': 'old-client-host', 'ORACLE_USER': 'olduser',
            'ORACLE_PASSWORD': 'old-secret', 'ORACLE_SCHEMA': 'IAS20241', 'ORACLE_SERVICE_NAME': 'OLD',
            'ONYX_BASE_URL': 'http://old-client-host:8090/Service/OnyxService.svc',
            'ONYX_DEFAULT_WAREHOUSE': '60', 'ONYX_COMPARE_WAREHOUSES': '1,30',
            'ONYX_ALLOWED_HOSTS': 'old-client-host', 'STOCK_QTY_SOURCE': 'oracle',
        }
        with patch.dict(os.environ, client_env):
            reloaded = importlib.reload(project_settings)
        try:
            oracle = reloaded.ORACLE
            api = reloaded.EXTERNAL_API
            self.assertFalse(oracle['ENABLED'])
            for key in ('HOST', 'USER', 'PASSWORD', 'SCHEMA', 'SERVICE_NAME'):
                self.assertEqual(oracle[key], '', key)
            self.assertEqual(api['BASE_URL'], '')
            self.assertEqual(api['DEFAULT_WAREHOUSE'], '')
            self.assertEqual(api['COMPARE_WAREHOUSES'], [])
            self.assertEqual(api['ALLOWED_HOSTS'], [])
            self.assertEqual(api['WAREHOUSES'], [])
            self.assertEqual(reloaded.STOCK_QTY_SOURCE, 'api')
        finally:
            importlib.reload(project_settings)


class HomeDashboardHelpersTests(TestCase):
    """حسابات لوحة الرئيسية التي تحدد معنى الأرقام المعروضة."""

    def test_previous_period_has_same_length_and_ends_day_before(self):
        from datetime import date

        from search.home_dashboard import previous_period

        self.assertEqual(
            previous_period(date(2026, 9, 1), date(2026, 9, 30)),
            (date(2026, 8, 2), date(2026, 8, 31)),
        )
        self.assertEqual(
            previous_period(date(2026, 3, 5), date(2026, 3, 5)),
            (date(2026, 3, 4), date(2026, 3, 4)),
        )

    def test_current_month_ends_today_and_closed_month_ends_on_last_day(self):
        from datetime import date

        from search.home_dashboard import month_bounds, recent_months

        today = date(2026, 9, 18)
        self.assertEqual(month_bounds(2026, 9, today), (date(2026, 9, 1), today))
        self.assertEqual(month_bounds(2026, 2, today), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(month_bounds(2025, 12, today), (date(2025, 12, 1), date(2025, 12, 31)))
        self.assertEqual(
            recent_months(6, date(2026, 2, 10)),
            ['2025-09', '2025-10', '2025-11', '2025-12', '2026-01', '2026-02'],
        )

    def test_channel_split_does_not_double_count_pos_mirrored_cash_invoices(self):
        from search.home_dashboard import split_channels
        from search.sales_dashboard import _combined_channel_totals

        pos = [
            {'branch_code': '1', 'sales_total': 1000, 'invoice_count': 10},
            {'branch_code': '2', 'sales_total': 500, 'invoice_count': 5},
        ]
        credit = [{'branch_code': '1', 'sales_total': 200, 'invoice_count': 2}]
        # فرع 1: نقدي الفواتير أقل من POS (انعكاس) · فرع 3: نقدي بلا نقاط بيع
        cash = [
            {'branch_code': '1', 'sales_total': 900, 'invoice_count': 9},
            {'branch_code': '2', 'sales_total': 650, 'invoice_count': 6},
            {'branch_code': '3', 'sales_total': 300, 'invoice_count': 3},
        ]
        split = split_channels(pos, credit, cash)
        self.assertEqual(split['pos'], 1500)
        self.assertEqual(split['credit'], 200)
        self.assertEqual(split['cash_extra'], 450)
        combined, _ = _combined_channel_totals(pos, credit, cash)
        self.assertEqual(split['total'], combined)

    def test_aging_keeps_fixed_bucket_order_and_shares(self):
        from search.home_dashboard import assemble_aging

        out = assemble_aging(
            [
                {'BUCKET': '90_plus', 'STOCK_VALUE': 25, 'ITEM_COUNT': 4},
                {'BUCKET': '0_30', 'STOCK_VALUE': 75, 'ITEM_COUNT': 9},
                {'BUCKET': 'unknown', 'STOCK_VALUE': 999, 'ITEM_COUNT': 1},
            ]
        )
        self.assertEqual([b['key'] for b in out], ['0_30', '31_60', '61_90', '90_plus'])
        self.assertEqual([b['share_pct'] for b in out], [75.0, 0.0, 0.0, 25.0])
        self.assertEqual(out[3]['item_count'], 4)

    def test_change_margin_and_turnover_do_not_invent_values(self):
        from search.home_dashboard import change_pct, margin_summary, turnover_summary

        self.assertIsNone(change_pct(100, 0))
        self.assertEqual(change_pct(110, 100), 10.0)
        self.assertEqual(margin_summary(200, 150), {'profit': 50.0, 'margin_pct': 25.0})
        self.assertIsNone(margin_summary(0, 0)['margin_pct'])
        self.assertEqual(
            turnover_summary(cost_of_sales=30, days=30, stock_value=365),
            {'turnover': 1.0, 'days_on_hand': 365},
        )
        self.assertEqual(
            turnover_summary(cost_of_sales=30, days=30, stock_value=0),
            {'turnover': None, 'days_on_hand': None},
        )


@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'home-tests'}}
)
class HomeDashboardPermissionTests(TestCase):
    """كل ودجت محكوم بصلاحية شاشته في الخادم، والواجهة لا تعرض إلا المسموح."""

    def setUp(self):
        from contextlib import nullcontext

        from django.core.cache import cache

        cache.clear()
        User = get_user_model()
        self.sales_user = User.objects.create_user(username='home-sales', password='StrongPassword123!')
        UserProfile.objects.create(
            user=self.sales_user, display_name='مبيعات', phone='0507100001', role_name='مدير مبيعات'
        )
        self.none_user = User.objects.create_user(username='home-none', password='StrongPassword123!')
        UserProfile.objects.create(user=self.none_user, display_name='بلا', phone='0507100002')
        UserNavPermission.objects.create(user=self.none_user, sections=[], blocked_screens=[])
        self.exec_user = User.objects.create_user(username='home-exec', password='StrongPassword123!')
        UserProfile.objects.create(
            user=self.exec_user, display_name='تنفيذي', phone='0507100003', role_name='رئيس تنفيذي'
        )

        patches = [
            patch('search.oracle_stock.oracle_enabled', return_value=True),
            patch('search.oracle_stock.oracle_session', side_effect=lambda: nullcontext()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _url(self, name):
        return reverse('home_widget_api', args=[name])

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self._url('sales'))
        self.assertEqual(response.status_code, 302)

    @patch('search.home_dashboard.build_sales_widget', return_value={'total': 10})
    def test_sales_manager_gets_sales_widget(self, build):
        self.client.force_login(self.sales_user)
        response = self.client.get(self._url('sales'), {'date_from': '2026-09-01', 'date_to': '2026-09-10'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data'], {'total': 10})
        args = build.call_args[0]
        self.assertEqual((args[0].isoformat(), args[1].isoformat()), ('2026-09-01', '2026-09-10'))

    @patch('search.home_dashboard.build_purchases_widget')
    @patch('search.home_dashboard.build_inventory_widget')
    @patch('search.home_dashboard.build_turnover_widget')
    def test_sales_manager_cannot_read_other_sections_directly(self, turnover, inventory, purchases):
        self.client.force_login(self.sales_user)
        for name in ('purchases', 'inventory', 'turnover', 'shortage'):
            response = self.client.get(self._url(name))
            self.assertEqual(response.status_code, 403, name)
        for mock in (turnover, inventory, purchases):
            mock.assert_not_called()

    @patch('search.home_dashboard.build_profit_widget')
    def test_blocked_profitability_screen_hides_profit(self, build):
        UserNavPermission.objects.create(
            user=self.sales_user, sections=['sales'], blocked_screens=['browse_profitability']
        )
        self.client.force_login(self.sales_user)
        self.assertEqual(self.client.get(self._url('profit')).status_code, 403)
        build.assert_not_called()

    @patch('search.home_dashboard.build_recent_widget', return_value={'rows': []})
    def test_recent_documents_limited_to_permitted_types(self, build):
        self.client.force_login(self.sales_user)
        self.assertEqual(self.client.get(self._url('recent')).status_code, 200)
        self.assertEqual(build.call_args[0][0], {'sale', 'return'})

    @patch('search.home_dashboard.build_month_point', return_value={'month': 'x'})
    def test_trend_validates_month_and_filters_series(self, build):
        from search.home_dashboard import recent_months

        self.client.force_login(self.sales_user)
        self.assertEqual(self.client.get(self._url('trend'), {'month': '1999-01'}).status_code, 400)
        self.assertEqual(self.client.get(self._url('trend'), {'month': 'abc'}).status_code, 400)
        month = recent_months(1)[0]
        self.assertEqual(self.client.get(self._url('trend'), {'month': month}).status_code, 200)
        self.assertEqual(build.call_args[0][2], {'sales'})

    def test_unknown_widget_is_404(self):
        self.client.force_login(self.exec_user)
        self.assertEqual(self.client.get(self._url('payroll')).status_code, 404)

    def test_invalid_period_is_400(self):
        self.client.force_login(self.exec_user)
        response = self.client.get(self._url('sales'), {'date_from': '2026-09-10', 'date_to': '2026-09-01'})
        self.assertEqual(response.status_code, 400)

    @patch('search.home_dashboard.build_sales_widget', side_effect=RuntimeError('ORA-12345 secret host'))
    def test_oracle_failure_returns_generic_error_without_details(self, build):
        self.client.force_login(self.exec_user)
        response = self.client.get(self._url('sales'))
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('ORA-12345', response.content.decode())

    def test_disabled_oracle_is_503(self):
        self.client.force_login(self.exec_user)
        with patch('search.oracle_stock.oracle_enabled', return_value=False):
            self.assertEqual(self.client.get(self._url('sales')).status_code, 503)

    def test_home_renders_only_permitted_sections(self):
        self.client.force_login(self.sales_user)
        html = self.client.get(reverse('home')).content.decode()
        self.assertIn('data-hx-card="sales"', html)
        self.assertIn('data-hx-card="top_items"', html)
        self.assertNotIn('data-hx-card="purchases"', html)
        self.assertNotIn('data-hx-card="inventory"', html)
        self.assertNotIn('id="hx-alerts"', html)

    def test_executive_sees_every_section(self):
        self.client.force_login(self.exec_user)
        html = self.client.get(reverse('home')).content.decode()
        for name in ('sales', 'purchases', 'inventory', 'profit', 'turnover', 'shortage', 'recent', 'trend'):
            self.assertIn(f'data-hx-card="{name}"', html, name)
        # بطاقة الرصيد الراكد حُذفت مع شاشة «رصيد بلا مبيعات»
        self.assertNotIn('data-hx-card="stagnant"', html)

    def test_user_without_sections_keeps_welcome_screen(self):
        self.client.force_login(self.none_user)
        html = self.client.get(reverse('home')).content.decode()
        self.assertNotIn('data-hx', html)
        self.assertIn('welcome-hero', html)


class RemovedScreensTests(TestCase):
    """الشاشات المحذوفة لم يعد لها مسار ولا رابط في الشريط."""

    def test_removed_routes_are_gone(self):
        from django.urls import NoReverseMatch

        for name in ('item_search', 'browse_unsold', 'browse_price_changes', 'sales_search', 'sync_barcodes', 'browse_performance'):
            with self.assertRaises(NoReverseMatch, msg=name):
                reverse(name)

    def test_sidebar_lists_only_remaining_screens(self):
        admin = get_user_model().objects.create_user(
            username='sidebar-admin', password='TestPass123!', is_staff=True, is_superuser=True
        )
        self.client.force_login(admin)
        html = self.client.get(reverse('home')).content.decode()
        for name in ('browse_sales', 'browse_profitability', 'browse_inventory', 'browse_purchases'):
            self.assertIn(f'href="{reverse(name)}"', html, name)
        self.assertNotIn('/items/', html)


class OracleLinkTests(TestCase):
    """وكيل الربط: توليد حزمة جهاز العميل وتقييد المفتاح وإلغاؤه."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.link_dir = Path(tmp.name) / 'oralink'
        self.link_dir.mkdir()
        (self.link_dir / 'ssh_host_ed25519_key.pub').write_text(
            'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostKeyForTests oracle-link-server\n', encoding='ascii'
        )
        override = override_settings(
            ORACLE_LINK_DIR=self.link_dir,
            ORACLE_LINK_PUBLIC_HOST='',
            ORACLE_LINK_SSH_PORT=2222,
            ORACLE_LINK_LOCAL_PORT=15210,
        )
        override.enable()
        self.addCleanup(override.disable)
        self.url = reverse('oracle_connection_settings')
        self.admin = get_user_model().objects.create_superuser(
            username='link-admin', password='StrongPassword123!', email='link@example.com'
        )

    def _authorized(self):
        return (self.link_dir / 'authorized_keys').read_text(encoding='ascii')

    def test_generate_returns_zip_with_key_and_restricted_authorized_key(self):
        import io
        import json
        import zipfile

        self.client.force_login(self.admin)
        response = self.client.post(self.url, {'action': 'link_generate'}, HTTP_HOST='72.61.107.230:7097')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')

        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            names = set(zf.namelist())
            for name in ('id_ed25519', 'known_hosts', 'config.json', 'install.ps1', 'tunnel.ps1', 'تشغيل الربط.bat'):
                self.assertIn(f'OracleLink/{name}', names)
            config = json.loads(zf.read('OracleLink/config.json'))
            known = zf.read('OracleLink/known_hosts').decode()
            private = zf.read('OracleLink/id_ed25519').decode()

        self.assertEqual(config['server'], '72.61.107.230')
        self.assertEqual(config['ssh_port'], 2222)
        self.assertEqual(config['remote_port'], 15210)
        self.assertTrue(known.startswith('[72.61.107.230]:2222 ssh-ed25519 '))
        self.assertIn('BEGIN OPENSSH PRIVATE KEY', private)

        line = self._authorized()
        self.assertTrue(line.startswith('restrict,port-forwarding,permitlisten="127.0.0.1:15210" ssh-ed25519 '))
        self.assertEqual(len(line.strip().splitlines()), 1)
        # المفتاح الخاص لا يُحفظ على الخادم
        for path in self.link_dir.iterdir():
            self.assertNotIn('PRIVATE KEY', path.read_text(encoding='ascii', errors='ignore'))

    def test_regenerate_replaces_previous_key(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, {'action': 'link_generate'})
        first = self._authorized()
        self.client.post(self.url, {'action': 'link_generate'})
        self.assertNotEqual(first, self._authorized())
        self.assertEqual(len(self._authorized().strip().splitlines()), 1)

    def test_revoke_clears_authorized_keys(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, {'action': 'link_generate'})
        response = self.client.post(self.url, {'action': 'link_revoke'})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertEqual(self._authorized(), '')

    def test_generate_fails_cleanly_without_server_host_key(self):
        (self.link_dir / 'ssh_host_ed25519_key.pub').unlink()
        self.client.force_login(self.admin)
        response = self.client.post(self.url, {'action': 'link_generate'})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertFalse((self.link_dir / 'authorized_keys').exists())

    def test_non_superuser_cannot_generate(self):
        user = get_user_model().objects.create_user(username='staff-only', password='StrongPassword123!', is_staff=True)
        self.client.force_login(user)
        response = self.client.post(self.url, {'action': 'link_generate'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse((self.link_dir / 'authorized_keys').exists())

    def test_screen_shows_link_section(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertContains(response, 'وكيل الربط')
        self.assertContains(response, 'غير مُولَّد')
        self.assertContains(response, 'data-port="15210"')

    def _wipe_link_dir(self):
        # محاكاة نشر جديد: مجلد البيانات داخل الحاوية يبدأ فارغاً
        for path in self.link_dir.iterdir():
            path.unlink()

    def test_generated_key_survives_redeploy(self):
        from search.oracle_link import restore_link_files

        restore_link_files()
        host_key = (self.link_dir / 'ssh_host_ed25519_key.pub').read_text(encoding='ascii')
        self.client.force_login(self.admin)
        self.client.post(self.url, {'action': 'link_generate'})
        authorized = self._authorized()

        self._wipe_link_dir()
        self.assertEqual(restore_link_files(), 'database')
        self.assertEqual(self._authorized(), authorized)
        self.assertEqual((self.link_dir / 'ssh_host_ed25519_key.pub').read_text(encoding='ascii'), host_key)
        self.assertIn('PRIVATE KEY', (self.link_dir / 'ssh_host_ed25519_key').read_text(encoding='ascii'))

    def test_first_restore_adopts_existing_host_key(self):
        from search.models import OracleLinkKeys
        from search.oracle_link import restore_link_files

        (self.link_dir / 'ssh_host_ed25519_key').write_text('-----BEGIN OPENSSH PRIVATE KEY-----\nx\n', encoding='ascii')
        self.assertEqual(restore_link_files(), 'disk')
        row = OracleLinkKeys.objects.get()
        self.assertIn('HostKeyForTests', row.host_public_key)

    def test_restore_generates_host_key_when_missing(self):
        from search.oracle_link import restore_link_files

        self._wipe_link_dir()
        self.assertEqual(restore_link_files(), 'generated')
        self.assertTrue((self.link_dir / 'ssh_host_ed25519_key.pub').read_text(encoding='ascii').startswith('ssh-ed25519 '))
        self.assertEqual(self._authorized(), '')

    def test_revoke_is_kept_after_redeploy(self):
        from search.oracle_link import restore_link_files

        restore_link_files()
        self.client.force_login(self.admin)
        self.client.post(self.url, {'action': 'link_generate'})
        self.client.post(self.url, {'action': 'link_revoke'})
        self._wipe_link_dir()
        restore_link_files()
        self.assertEqual(self._authorized(), '')

    def test_screen_warns_when_secret_key_not_from_env(self):
        self.client.force_login(self.admin)
        with override_settings(DEBUG=False, SECRET_KEY_FROM_ENV=False):
            self.assertContains(self.client.get(self.url), 'مفتاح التشفير غير ثابت')
        with override_settings(DEBUG=False, SECRET_KEY_FROM_ENV=True):
            self.assertNotContains(self.client.get(self.url), 'مفتاح التشفير غير ثابت')

    def test_screen_shows_active_key_fingerprint(self):
        from search.oracle_link import key_fingerprint, restore_link_files

        restore_link_files()
        self.client.force_login(self.admin)
        self.client.post(self.url, {'action': 'link_generate'})
        fingerprint = key_fingerprint(self._authorized())
        self.assertTrue(fingerprint.startswith('SHA256:'))
        self.assertEqual(len(fingerprint), len('SHA256:') + 43)
        response = self.client.get(self.url)
        self.assertContains(response, 'المفتاح المفعّل')
        self.assertContains(response, fingerprint)
        self.assertNotContains(response, 'PRIVATE KEY')

    def _dev_zip(self):
        import io
        import zipfile

        self.client.force_login(self.admin)
        response = self.client.post(self.url, {'action': 'dev_generate'}, HTTP_HOST='69.62.108.130:8081')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        return zipfile.ZipFile(io.BytesIO(response.content))

    def test_dev_key_package_and_restricted_line(self):
        with self._dev_zip() as zf:
            names = set(zf.namelist())
            bat = zf.read('OracleDev/تشغيل نفق المطوّر.bat').decode('ascii')
            known = zf.read('OracleDev/known_hosts').decode()
            private = zf.read('OracleDev/id_ed25519').decode()
        self.assertIn('OracleDev/اقرأني.txt', names)
        self.assertIn('-L 127.0.0.1:15210:127.0.0.1:15210 oralink@69.62.108.130', bat)
        self.assertIn('-p 2222', bat)
        self.assertNotIn('->', bat)
        self.assertTrue(known.startswith('[69.62.108.130]:2222 ssh-ed25519 '))
        self.assertIn('BEGIN OPENSSH PRIVATE KEY', private)
        line = self._authorized().strip()
        self.assertTrue(
            line.startswith('restrict,port-forwarding,permitopen="127.0.0.1:15210",permitlisten="127.0.0.1:1" ssh-ed25519 ')
        )
        self.assertTrue(line.endswith(' oracle-link-dev'))

    def test_agent_and_dev_keys_are_independent(self):
        from search.oracle_link import restore_link_files

        restore_link_files()
        self.client.force_login(self.admin)
        self.client.post(self.url, {'action': 'link_generate'})
        self._dev_zip().close()
        lines = self._authorized().strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith('oracle-link'))
        self.assertTrue(lines[1].endswith('oracle-link-dev'))

        # إلغاء ربط العميل لا يمس مفتاح المطوّر، والعكس
        self.client.post(self.url, {'action': 'link_revoke'})
        self.assertEqual(self._authorized().strip().splitlines(), [lines[1]])
        self.client.post(self.url, {'action': 'link_generate'})
        self.client.post(self.url, {'action': 'dev_revoke'})
        remaining = self._authorized().strip().splitlines()
        self.assertEqual(len(remaining), 1)
        self.assertTrue(remaining[0].endswith('oracle-link'))

    def test_dev_key_survives_redeploy(self):
        from search.oracle_link import restore_link_files

        restore_link_files()
        self._dev_zip().close()
        dev_line = self._authorized()
        self._wipe_link_dir()
        restore_link_files()
        self.assertEqual(self._authorized(), dev_line)
        response = self.client.get(self.url)
        self.assertContains(response, 'وصول المطوّر')
        self.assertContains(response, 'توليد مفتاح مطوّر جديد')

    def test_non_superuser_cannot_generate_dev_key(self):
        user = get_user_model().objects.create_user(username='staff-dev', password='StrongPassword123!', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.post(self.url, {'action': 'dev_generate'}).status_code, 403)
        self.assertFalse((self.link_dir / 'authorized_keys').exists())


class StagnantTreemapTests(TestCase):
    """خريطة الراكد: الكتل تملأ المساحة بالكامل بلا تداخل، ومساحتها متناسبة مع الكمية."""

    def test_tiles_fill_area_proportionally_without_overlap(self):
        from search.inventory_insights import _MAP_H, _MAP_W, _squarify

        values = [80512, 55537, 22796, 16052, 14304, 6744, 5652, 4783, 3000, 1200]
        scale = _MAP_W * _MAP_H / sum(values)
        rects = _squarify([v * scale for v in values], 0, 0, _MAP_W, _MAP_H)
        self.assertEqual(len(rects), len(values))
        for (x, y, w, h), v in zip(rects, values):
            self.assertGreaterEqual(x, -1e-6)
            self.assertGreaterEqual(y, -1e-6)
            self.assertLessEqual(x + w, _MAP_W + 1e-6)
            self.assertLessEqual(y + h, _MAP_H + 1e-6)
            self.assertAlmostEqual(w * h, v * scale, places=6)
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                overlap_w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
                overlap_h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
                self.assertFalse(overlap_w > 1e-6 and overlap_h > 1e-6)

    def test_tile_levels_follow_value_and_style_strings(self):
        from search.inventory_insights import _stagnant_tiles

        rows = [
            {'qty_total': 100, 'stock_value': 10},
            {'qty_total': 50, 'stock_value': 400},
            {'qty_total': 0, 'stock_value': 5},
        ]
        _stagnant_tiles(rows)
        self.assertEqual(rows[0]['tile']['level'], 1)
        self.assertEqual(rows[1]['tile']['level'], 4)
        self.assertNotIn('tile', rows[2])
        self.assertTrue(rows[0]['tile']['right'].endswith('%'))
        self.assertNotIn(',', rows[0]['tile']['width'])


class InventoryCompositionTests(TestCase):
    def test_top_groups_then_rest_with_shares(self):
        from search.inventory_insights import _value_composition

        groups = [{'name': '( الغذائية )', 'stock_value': 600}, {'name': 'ب', 'stock_value': 300}]
        groups += [{'name': f'م{i}', 'stock_value': 20} for i in range(5)]
        parts = _value_composition(groups, 1000, top=2)
        self.assertEqual([p['name'] for p in parts], ['الغذائية', 'ب', 'أخرى'])
        self.assertEqual([p['tone'] for p in parts], ['lead', 'g1', 'rest'])
        self.assertEqual([p['share_display'] for p in parts], ['60.0%', '30.0%', '10.0%'])
        self.assertEqual(parts[0]['width'], '60.000%')
        self.assertEqual(_value_composition(groups, 0), [])


class PurchaseItemsParetoTests(TestCase):
    def test_bars_right_to_left_and_cumulative_line(self):
        from search.views import _PARETO_H, _PARETO_W, _items_pareto

        rows = [
            {'name': 'أ', 'amount': 300, 'share_pct': 30.0, 'amount_display': '300.00', 'share_display': '30.0%', 'qty_display': '3', 'invoice_count': 3},
            {'name': 'ب', 'amount': 100, 'share_pct': 10.0, 'amount_display': '100.00', 'share_display': '10.0%', 'qty_display': '1', 'invoice_count': 1},
            {'name': 'ج', 'amount': 0, 'share_pct': 0.0},
        ]
        p = _items_pareto(rows)
        self.assertEqual(p['count'], 2)
        self.assertEqual(p['cum_display'], '40.0%')
        first, second = p['bars']
        self.assertTrue(first['lead'])
        self.assertGreater(float(first['x']), float(second['x']))  # الأكبر يميناً
        self.assertAlmostEqual(float(first['h']), _PARETO_H - 18.0, places=1)
        self.assertAlmostEqual(float(p['end_y']), 18.0, places=1)  # آخر نقطة في الأعلى
        self.assertTrue(all(',' not in b['x'] for b in p['bars']))
        self.assertLessEqual(float(first['x']) + float(first['w']), _PARETO_W)
        self.assertIsNone(_items_pareto([]))


class PurchaseDefaultPeriodTests(TestCase):
    @patch('search.oracle_stock.oracle_enabled', return_value=False)
    def test_default_period_starts_at_month_start(self, _enabled):
        from datetime import date

        admin = get_user_model().objects.create_user(
            username='pur-admin', password='TestPass123!', is_staff=True, is_superuser=True
        )
        self.client.force_login(admin)
        html = self.client.get(reverse('browse_purchases')).content.decode()
        today = date.today()
        self.assertIn(f'name="date_from" value="{today.replace(day=1).isoformat()}"', html)
        self.assertIn(f'name="date_to" value="{today.isoformat()}"', html)


class VendorDebtsTests(TestCase):
    def test_top_debts_skip_credit_balances_and_share_of_total(self):
        from datetime import datetime

        from search import oracle_purchases

        rows = [
            {'CODE': '282', 'NAME': 'مورد أ', 'BALANCE': 300.0, 'LAST_DATE': datetime(2026, 10, 19),
             'TOTAL_DEBT': 400.0, 'DEBT_COUNT': 2},
            {'CODE': '127', 'NAME': 'مورد ب', 'BALANCE': 100.0, 'LAST_DATE': datetime(2026, 9, 24),
             'TOTAL_DEBT': 400.0, 'DEBT_COUNT': 2},
            {'CODE': '3', 'NAME': 'مورد مدفوع مقدماً', 'BALANCE': -50.0, 'LAST_DATE': None,
             'TOTAL_DEBT': 400.0, 'DEBT_COUNT': 2},
        ]
        with patch.object(oracle_purchases, '_fetch_all', return_value=rows), \
                patch.object(oracle_purchases, '_schema', return_value='S'), \
                patch.object(oracle_purchases.cache, 'get', return_value=None), \
                patch.object(oracle_purchases.cache, 'set'):
            result = oracle_purchases.fetch_vendor_debts(limit=3)

        self.assertEqual([r['code'] for r in result['rows']], ['282', '127'])
        self.assertEqual(result['rows'][0]['share_display'], '75.0%')
        self.assertEqual(result['rows'][0]['last_date'], '2026-10-19')
        self.assertEqual(result['total_display'], '400.00')
        self.assertEqual(result['top_total_display'], '400.00')
        self.assertEqual(result['debt_count'], 2)


class ProfitabilityTests(TestCase):
    """الربحية: قائمة الدخل والمركز المالي من قيود الترحيل، واستبدال شاشة الأداء."""

    RAW = {
        'names': {'531': 'تكاليف العاملين', '538': 'مصاريف أخرى', '161': 'المخزون السلعي',
                  '168': 'النقدية', '223': 'موردون'},
        'pnl': [
            {'k3': '411', 'cur': -1000.0, 'prev': -800.0, 'ytd': -5000.0},
            {'k3': '511', 'cur': 800.0, 'prev': 600.0, 'ytd': 4000.0},
            {'k3': '521', 'cur': 50.0, 'prev': 40.0, 'ytd': 250.0},
            {'k3': '531', 'cur': 120.0, 'prev': 100.0, 'ytd': 600.0},
            {'k3': '538', 'cur': 80.0, 'prev': 60.0, 'ytd': 400.0},
            {'k3': '471', 'cur': -10.0, 'prev': 0.0, 'ytd': -10.0},
        ],
        'monthly': [
            {'mm': 1, 'k2': '41', 'amt': -1000.0},
            {'mm': 1, 'k2': '51', 'amt': 800.0},
            {'mm': 1, 'k2': '53', 'amt': 300.0},
        ],
        # أصول 1000، التزامات 900، حقوق ملكية 300، نتيجة السنة −240 → فرق افتتاحي 40
        'balance': {'161': 600.0, '168': 100.0, '113': 300.0, '223': -700.0, '211': -200.0, '311': -300.0},
    }

    def _build(self, d_from, d_to):
        from search import oracle_finance

        with patch.object(oracle_finance, '_fetch_raw', return_value=self.RAW), \
                patch.object(oracle_finance, '_fetch_trial', return_value=[]), \
                patch.object(oracle_finance.cache, 'get', return_value=None), \
                patch.object(oracle_finance.cache, 'set'):
            return oracle_finance.build_profitability(d_from, d_to)

    def test_previous_span_compares_like_months(self):
        from datetime import date

        from search.oracle_finance import previous_span

        self.assertEqual(previous_span(date(2026, 9, 1), date(2026, 9, 30)), (date(2026, 8, 1), date(2026, 8, 31)))
        self.assertEqual(previous_span(date(2026, 10, 1), date(2026, 10, 23)), (date(2026, 9, 1), date(2026, 9, 23)))
        self.assertEqual(previous_span(date(2026, 3, 1), date(2026, 3, 31)), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertIsNone(previous_span(date(2026, 1, 1), date(2026, 10, 23)))

    def test_income_statement_lines_and_margins(self):
        result = self._build('2026-09-01', '2026-09-30')
        lines = {line['label']: line for line in result['statement']}
        self.assertEqual(lines['مجمل الربح']['cur_display'], '200.00')
        self.assertEqual(lines['مصروفات عمومية وإدارية']['cur_display'], '200.00')
        self.assertEqual(lines['الربح التشغيلي']['cur_display'], '-50.00')
        self.assertEqual(lines['صافي الربح (الخسارة)']['cur_display'], '-40.00')
        self.assertEqual(lines['صافي الربح (الخسارة)']['kind'], 'total')
        # المبيعات +25% عن السابقة: صعود مرغوب؛ وتكلفة المبيعات +33.3%: صعود غير مرغوب
        self.assertEqual(lines['إيرادات النشاط (المبيعات)']['change_display'], '+25.0%')
        self.assertEqual(lines['إيرادات النشاط (المبيعات)']['change_tone'], 'up')
        self.assertEqual(lines['تكلفة المبيعات']['change_tone'], 'down')
        self.assertEqual(result['kpis']['gross_margin'], '20.0%')
        self.assertEqual(result['kpis']['net_margin'], '-4.0%')
        self.assertTrue(result['flow']['overflow'])
        self.assertTrue(any('خاسرة' in n['text'] for n in result['reading']))

    def test_financial_position_shows_unbalanced_opening_gap(self):
        pos = self._build('2026-01-01', '2026-09-30')['position']
        self.assertEqual(pos['display']['total_assets'], '1,000.00')
        self.assertEqual(pos['display']['total_liab'], '900.00')
        self.assertEqual(pos['display']['ytd_net'], '-240.00')
        self.assertEqual(pos['display']['gap'], '40.00')
        self.assertEqual(pos['display']['total_right'], pos['display']['total_assets'])
        self.assertEqual(pos['share']['current_assets'], '70.0%')
        self.assertEqual([p['name'] for p in pos['current_assets_parts']], ['المخزون السلعي', 'النقدية'])
        self.assertEqual(pos['non_current_assets_parts'][0]['share'], '30.0%')

    def test_trial_balance_closing_per_account_and_side(self):
        from search.oracle_finance import _trial_balance

        rows = [
            # نقدية: أول المدة 100 مدين + 50 مدين − 30 دائن = 120 مدين
            {'CODE': '1681010001', 'NAME': 'الصندوق', 'OPENING': 100.0, 'DR': 50.0, 'CR': 30.0},
            # مورد: يبدأ 100 دائن ويُسدَّد 30 → 70 دائن
            {'CODE': '2232010001', 'NAME': 'الموردون', 'OPENING': -100.0, 'DR': 30.0, 'CR': 0.0},
            {'CODE': '4111010001', 'NAME': 'المبيعات', 'OPENING': 0.0, 'DR': 0.0, 'CR': 50.0},
            # حساب بلا رصيد ولا حركة يُستبعد
            {'CODE': '5111010009', 'NAME': 'خامل', 'OPENING': 0.0, 'DR': 0.0, 'CR': 0.0},
        ]
        tb = _trial_balance(rows)
        self.assertEqual(tb['count'], 3)
        self.assertEqual([g['name'] for g in tb['groups']], ['الأصول', 'الخصوم', 'الإيرادات'])
        cash = tb['groups'][0]['rows'][0]
        self.assertEqual((cash['closing']['display'], cash['closing']['side']), ('120.00', 'مدين'))
        supplier = tb['groups'][1]['rows'][0]
        self.assertEqual((supplier['opening']['display'], supplier['opening']['side']), ('100.00', 'دائن'))
        self.assertEqual((supplier['closing']['display'], supplier['closing']['side']), ('70.00', 'دائن'))
        self.assertEqual(tb['totals']['dr'], '80.00')
        self.assertEqual(tb['totals']['cr'], '80.00')
        self.assertEqual(tb['totals']['close_dr'], '120.00')
        self.assertEqual(tb['totals']['close_cr'], '120.00')
        self.assertTrue(tb['balanced'])

    def test_monthly_rows_end_with_totals_row(self):
        monthly = self._build('2026-01-01', '2026-09-30')['monthly']
        self.assertEqual(monthly['rows'][0]['month'], 'يناير')
        self.assertEqual(monthly['total']['month'], 'الإجمالي')
        self.assertEqual(monthly['total']['revenue_display'], '1,000')
        self.assertEqual(monthly['total']['net_display'], '-100')
        self.assertEqual(monthly['total']['margin_display'], '20.0%')

    def test_performance_url_redirects_and_sidebar_shows_profitability(self):
        admin = get_user_model().objects.create_user(
            username='pf-admin', password='TestPass123!', is_staff=True, is_superuser=True
        )
        self.client.force_login(admin)
        response = self.client.get('/sales/performance/')
        self.assertRedirects(response, reverse('browse_profitability'), fetch_redirect_response=False)
        with patch('search.oracle_stock.oracle_enabled', return_value=False):
            html = self.client.get(reverse('browse_profitability')).content.decode()
        self.assertIn('href="/profitability/"', html)
        self.assertNotIn('تحليل الأداء', html)
        self.assertIn('أوراكل غير مفعّل', html)


class UserManagementTests(TestCase):
    """شاشة المستخدمين والأدوار: سوبر أدمن، مدير النظام، مدير مالي، مدير شراء."""

    def setUp(self):
        self.root = get_user_model().objects.create_user(
            username='0500000001', password='TestPass123!', first_name='الجذر',
            is_staff=True, is_superuser=True,
        )
        UserProfile.objects.create(user=self.root, display_name='الجذر', phone='0500000001', role_name='سوبر أدمن')

    def _create(self, actor, **data):
        payload = {'name': 'مستخدم', 'phone': '0501112223', 'role_name': 'مدير شراء',
                   'password': 'Strong-Pass-9', 'is_active': 'on'}
        payload.update(data)
        self.client.force_login(actor)
        return self.client.post(reverse('user_create'), payload)

    def test_roles_grant_expected_screens(self):
        from search.nav_permissions import user_can_access_screen

        self._create(self.root, name='شراء', phone='0501000001', role_name='مدير شراء')
        self._create(self.root, name='مالي', phone='0501000002', role_name='مدير مالي')
        buyer = get_user_model().objects.get(profile__phone='0501000001')
        finance = get_user_model().objects.get(profile__phone='0501000002')
        for screen in ('browse_purchases', 'browse_inventory'):
            self.assertTrue(user_can_access_screen(buyer, screen), screen)
        for screen in ('browse_sales', 'browse_profitability', 'user_list'):
            self.assertFalse(user_can_access_screen(buyer, screen), screen)
        for screen in ('browse_profitability', 'browse_sales', 'browse_purchases', 'browse_inventory'):
            self.assertTrue(user_can_access_screen(finance, screen), screen)
        self.assertFalse(user_can_access_screen(finance, 'user_list'))
        self.assertFalse(buyer.is_staff or finance.is_staff)

    def test_system_manager_manages_users_but_not_super_admin(self):
        response = self._create(self.root, name='نظام', phone='0501000003', role_name='مدير النظام')
        self.assertRedirects(response, reverse('user_list'))
        manager = get_user_model().objects.get(profile__phone='0501000003')
        self.assertTrue(manager.is_staff)
        self.assertFalse(manager.is_superuser)

        self.client.force_login(manager)
        html = self.client.get(reverse('user_list')).content.decode()
        self.assertIn('href="/users/"', html)
        self.assertNotIn('href="/settings/oracle/"', html)
        # لا يمنح دور السوبر أدمن، ولا يعدّل حساب سوبر أدمن
        response = self._create(manager, name='محاولة', phone='0501000004', role_name='سوبر أدمن')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(profile__phone='0501000004').exists())
        # حساب السوبر أدمن مخفي عنه: لا يظهر في القائمة، ورابطه المباشر «غير موجود»
        self.assertEqual(self.client.get(reverse('user_edit', args=[self.root.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('user_delete', args=[self.root.pk])).status_code, 404)
        html = self.client.get(reverse('user_list')).content.decode()
        self.assertNotIn('0500000001', html)
        self.assertNotIn('us-tone-super', html)
        self.assertIn('<span class="us-person-name">نظام', html)

    def test_oracle_link_is_super_admin_only(self):
        self._create(self.root, name='نظام', phone='0501000008', role_name='مدير النظام')
        self.assertIn('href="/settings/oracle/"', self.client.get(reverse('user_list')).content.decode())
        manager = get_user_model().objects.get(profile__phone='0501000008')
        self.client.force_login(manager)
        self.assertNotIn('href="/settings/oracle/"', self.client.get(reverse('user_list')).content.decode())
        self.assertEqual(self.client.get(reverse('oracle_connection_settings')).status_code, 403)

    def test_non_staff_cannot_open_users(self):
        self._create(self.root, name='شراء', phone='0501000005', role_name='مدير شراء')
        buyer = get_user_model().objects.get(profile__phone='0501000005')
        self.client.force_login(buyer)
        self.assertRedirects(self.client.get(reverse('user_list')), reverse('home'))

    def test_cannot_lock_yourself_or_remove_last_super_admin(self):
        self.client.force_login(self.root)
        response = self.client.post(reverse('user_edit', args=[self.root.pk]), {
            'name': 'الجذر', 'phone': '0500000001', 'role_name': 'مدير شراء', 'password': '',
        })
        self.assertEqual(response.status_code, 200)
        self.root.refresh_from_db()
        self.assertTrue(self.root.is_superuser and self.root.is_active)
        response = self.client.post(reverse('user_delete', args=[self.root.pk]))
        self.assertTrue(get_user_model().objects.filter(pk=self.root.pk).exists())

    def test_edit_role_and_delete_user(self):
        self._create(self.root, name='شراء', phone='0501000006', role_name='مدير شراء')
        user = get_user_model().objects.get(profile__phone='0501000006')
        response = self.client.post(reverse('user_edit', args=[user.pk]), {
            'name': 'شراء', 'phone': '0501000006', 'role_name': 'مدير مالي', 'password': '',
        })
        self.assertRedirects(response, reverse('user_list'))
        user.refresh_from_db()
        self.assertFalse(user.is_active)  # المفتاح أُطفئ في الطلب
        self.assertEqual(user.profile.role_name, 'مدير مالي')
        self.assertEqual(sorted(user.nav_permission.sections), ['finance', 'inventory', 'purchases', 'sales'])
        self.assertContains(self.client.get(reverse('user_delete', args=[user.pk])), 'حذف «شراء»؟')
        self.assertRedirects(self.client.post(reverse('user_delete', args=[user.pk])), reverse('user_list'))
        self.assertFalse(get_user_model().objects.filter(pk=user.pk).exists())

    def test_list_shows_role_cards_and_filters(self):
        self._create(self.root, name='شراء', phone='0501000007', role_name='مدير شراء')
        html = self.client.get(reverse('user_list'), {'role': 'مدير شراء'}).content.decode()
        self.assertIn('us-role-card', html)
        self.assertIn('شراء', html)
        self.assertNotIn('<span class="us-person-name">الجذر', html)


class HrScreenTests(TestCase):
    """الموارد البشرية: رواتب الأفراد لأدوار الإدارة فقط."""

    def _user(self, phone, role, **flags):
        user = get_user_model().objects.create_user(username=phone, password='TestPass123!', first_name=role, **flags)
        UserProfile.objects.create(user=user, display_name=role, phone=phone, role_name=role)
        return user

    def test_hr_visible_to_admins_only(self):
        from search.nav_permissions import user_can_access_screen
        from search.nav_permissions import ROLE_SECTIONS

        admin = self._user('0502000001', 'مدير النظام', is_staff=True)
        finance = self._user('0502000002', 'مدير مالي')
        root = self._user('0502000003', 'سوبر أدمن', is_staff=True, is_superuser=True)
        self.assertFalse(user_can_access_screen(admin, 'browse_hr'))
        self.assertTrue(user_can_access_screen(root, 'browse_hr'))
        self.assertFalse(user_can_access_screen(finance, 'browse_hr'))
        self.assertNotIn('hr', ROLE_SECTIONS['مدير مالي'])
        self.assertNotIn('hr', ROLE_SECTIONS['مدير شراء'])

    def test_months_helpers(self):
        from datetime import date

        from search.oracle_hr import _months_word, whole_months

        self.assertEqual(whole_months(date(2026, 1, 15), date(2026, 10, 23)), (date(2026, 1, 1), date(2026, 11, 1)))
        self.assertEqual([_months_word(n) for n in (1, 2, 7, 12)], ['شهر واحد', 'شهران', '7 أشهر', '12 شهراً'])


class SafeSessionMiddlewareTests(TestCase):
    def test_interrupted_session_returns_401_json_for_ajax(self):
        from django.contrib.sessions.exceptions import SessionInterrupted
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        from config.session_middleware import SafeSessionMiddleware

        request = RequestFactory().get('/home/api/widget/trend/', HTTP_ACCEPT='application/json')
        request.session = None
        with patch.object(SessionMiddleware, 'process_response', side_effect=SessionInterrupted('x')):
            response = SafeSessionMiddleware(lambda r: None).process_response(request, None)
        self.assertEqual(response.status_code, 401)


class UserActivityTests(TestCase):
    def _user(self, phone, role, **flags):
        user = get_user_model().objects.create_user(username=phone, password='TestPass123!', first_name=role, **flags)
        UserProfile.objects.create(user=user, display_name=role, phone=phone, role_name=role)
        return user

    def test_login_and_screen_views_are_recorded(self):
        from search.models import UserActivitySession, UserScreenView

        self._user('0503000001', 'مدير مالي')
        self.client.login(username='0503000001', password='TestPass123!')
        self.client.get('/purchases/')
        self.client.get('/purchases/')  # نفس الشاشة خلال دقيقة: لا تتكرر
        self.client.get('/inventory/')

        labels = list(UserScreenView.objects.order_by('viewed_at').values_list('screen_label', flat=True))
        self.assertEqual(labels, ['المشتريات والموردون', 'أرصدة المخزون'])

    def test_activity_screen_is_staff_only(self):
        self._user('0503000002', 'مدير مالي')
        self.client.login(username='0503000002', password='TestPass123!')
        self.assertEqual(self.client.get('/users/activity/').status_code, 302)

    def test_activity_screen_is_superuser_only(self):
        manager = self._user('0503000003', 'مدير النظام', is_staff=True)
        self.client.login(username=manager.username, password='TestPass123!')
        self.assertEqual(self.client.get('/users/activity/').status_code, 302)

    def test_activity_screen_lists_sessions_for_superuser(self):
        root = self._user('0503000004', 'سوبر أدمن', is_staff=True, is_superuser=True)
        worker = self._user('0503000005', 'مدير شراء')

        c = self.client_class()
        c.login(username=worker.username, password='TestPass123!')
        c.get('/purchases/')

        self.client.login(username=root.username, password='TestPass123!')
        response = self.client.get('/users/activity/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'مدير شراء')
        self.assertContains(response, 'المشتريات والموردون')


class HomeWidgetCacheTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        # لا نمسح الكاش كله: نحذف مفاتيح هذه الاختبارات فقط
        cache.delete_many([f'home:v4:{n}:a' for n in ('t_closed', 't_live')])

    def test_live_widgets_are_never_cached(self):
        from search import home_dashboard as hd

        calls = []

        def build():
            calls.append(1)
            return {'n': len(calls)}

        self.assertEqual(hd.cached_widget('t_live', ('a',), hd.LIVE_TTL, build), {'n': 1})
        self.assertEqual(hd.cached_widget('t_live', ('a',), hd.LIVE_TTL, build), {'n': 2})

    def test_closed_period_is_cached(self):
        from search import home_dashboard as hd

        calls = []

        def build():
            calls.append(1)
            return {'n': len(calls)}

        first = hd.cached_widget('t_closed', ('a',), hd.CLOSED_TTL, build)
        second = hd.cached_widget('t_closed', ('a',), hd.CLOSED_TTL, build)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_today_period_uses_live_ttl(self):
        from datetime import date, timedelta

        from search import home_dashboard as hd

        today = date.today()
        self.assertEqual(hd._ttl_for(today, today), 0)
        self.assertEqual(hd._ttl_for(today - timedelta(days=1), today), hd.CLOSED_TTL)
