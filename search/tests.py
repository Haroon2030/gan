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
        self.assertContains(response, 'تحليل الأداء')
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

    def test_changing_env_password_recovers_bootstrap_login(self):
        with patch.dict(
            'os.environ',
            {'APP_LOGIN_USERNAME': 'admin', 'APP_LOGIN_PASSWORD': 'OldPassword123!'},
        ):
            call_command('ensure_app_user')

        with patch.dict(
            'os.environ',
            {'APP_LOGIN_USERNAME': 'admin', 'APP_LOGIN_PASSWORD': 'TestPass123!'},
        ):
            call_command('ensure_app_user')

        user = get_user_model().objects.get(username='admin')
        self.assertTrue(user.check_password('TestPass123!'))
        self.assertTrue(self.client.login(username='admin', password='TestPass123!'))

    def test_ui_created_user_password_survives_bootstrap(self):
        staff = get_user_model().objects.create_user(
            username='0501111111',
            password='StaffPass123!',
            is_staff=True,
        )
        UserProfile.objects.create(
            user=staff, display_name='مشرف', phone='0501111111'
        )

        with patch.dict(
            'os.environ',
            {'APP_LOGIN_USERNAME': 'admin', 'APP_LOGIN_PASSWORD': 'TestPass123!'},
        ):
            call_command('ensure_app_user')

        staff.refresh_from_db()
        self.assertTrue(staff.check_password('StaffPass123!'))

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
    def test_blocked_performance_screen_hides_profit(self, build):
        UserNavPermission.objects.create(
            user=self.sales_user, sections=['sales'], blocked_screens=['browse_performance']
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

        for name in ('item_search', 'browse_unsold', 'browse_price_changes', 'sales_search', 'sync_barcodes'):
            with self.assertRaises(NoReverseMatch, msg=name):
                reverse(name)

    def test_sidebar_lists_only_remaining_screens(self):
        admin = get_user_model().objects.create_user(
            username='sidebar-admin', password='TestPass123!', is_staff=True, is_superuser=True
        )
        self.client.force_login(admin)
        html = self.client.get(reverse('home')).content.decode()
        for name in ('browse_sales', 'browse_performance', 'browse_inventory', 'browse_purchases'):
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
