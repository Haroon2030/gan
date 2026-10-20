from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from django.conf import settings

from search.models import UserActivitySession, UserNavPermission, UserProfile
from search.validators import contains_sql_injection, sanitize_search_query, ValidationError

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

    def test_sanitize_search_rejects_sqli(self):
        with self.assertRaises(ValidationError):
            sanitize_search_query("' OR 1=1--")

    def test_sanitize_allows_arabic_item_names(self):
        self.assertEqual(sanitize_search_query('حليب طازج'), 'حليب طازج')
        self.assertEqual(sanitize_search_query('تمر (سكرة)'), 'تمر (سكرة)')

    def test_search_endpoint_blocks_sqli_query(self):
        response = self.client.get(reverse('item_search'), {'q': "' OR 1=1--"})
        self.assertEqual(response.status_code, 403)

    def test_normal_barcode_search_still_allowed(self):
        response = self.client.get(reverse('item_search'), {'q': '06100'})
        self.assertEqual(response.status_code, 200)


class NameSearchTests(TestCase):
    def setUp(self):
        from search.models import ItemBarcode

        self.user = get_user_model().objects.create_user(
            username='namesearcher',
            password='StrongPassword123!',
        )
        self.client.force_login(self.user)
        ItemBarcode.objects.create(
            barcode='1001',
            item_code='A100',
            name='حليب طازج كامل الدسم',
            unit='حبة',
            pack_size='1',
        )
        ItemBarcode.objects.create(
            barcode='1002',
            item_code='A101',
            name='حليب قليل الدسم',
            unit='حبة',
            pack_size='1',
        )
        ItemBarcode.objects.create(
            barcode='2001',
            item_code='B200',
            name='تمر سكرة',
            unit='كيلو',
            pack_size='1',
        )

    def test_name_search_returns_matching_list(self):
        response = self.client.get(reverse('item_search'), {'q': 'حليب'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['match_type'], 'name_list')
        codes = {item['code'] for item in response.context['items']}
        self.assertEqual(codes, {'A100', 'A101'})

    def test_unique_name_resolves_single_item(self):
        with patch('search.views.search_item_details', return_value=[]):
            response = self.client.get(reverse('item_search'), {'q': 'تمر سكرة'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['match_type'], 'name')
        self.assertEqual(response.context['items'][0]['code'], 'B200')


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

    def test_sync_endpoint_requires_login(self):
        response = self.client.post(reverse('sync_barcodes'))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))

    def test_authenticated_user_can_open_search(self):
        user = get_user_model().objects.create_user(
            username='tester',
            password='StrongPassword123!',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('item_search'))

        self.assertEqual(response.status_code, 200)

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



class VendorTurnoverBindTests(TestCase):
    def test_item_and_vendor_codes_bind_as_text(self):
        from search.oracle_vendor_turnover import _bind_vendor, _icode_in

        _, params = _icode_in(['06100', '2326785'])
        self.assertEqual(params['c0'], '06100')
        self.assertEqual(params['c1'], '2326785')
        self.assertEqual(_bind_vendor('2326785'), '2326785')
        self.assertIsInstance(_bind_vendor('2326785'), str)


class VendorTurnoverCostAdjTests(TestCase):
    def test_prefers_matching_vendor_and_skips_other(self):
        from datetime import datetime

        from search.oracle_vendor_turnover import _index_cost_adj, _pick_cost_adj

        by_item = _index_cost_adj(
            [
                {
                    'i_code': '06100',
                    'v_code': '999',
                    'inc_cost': 1.0,
                    'stk_desc': 'مورد آخر',
                    'doc_date': datetime(2026, 3, 1),
                    'doc_ser': 1,
                },
                {
                    'i_code': '06100',
                    'v_code': '2326785',
                    'inc_cost': 4.48,
                    'stk_desc': 'تسوية تكاليف فاتورة شراء',
                    'doc_date': datetime(2026, 2, 1),
                    'doc_ser': 2,
                },
            ]
        )
        hit = _pick_cost_adj('06100', '2326785', by_item)
        self.assertIsNotNone(hit)
        self.assertTrue(hit['cost_adj'])
        self.assertEqual(hit['cost_adj_label'], 'نعم')
        self.assertEqual(hit['cost_adj_cost'], 4.48)
        self.assertEqual(hit['cost_adj_count'], 1)
        self.assertIsNone(_pick_cost_adj('06100', '111', by_item))

    def test_blank_vendor_attaches_when_no_match(self):
        from search.oracle_vendor_turnover import _index_cost_adj, _pick_cost_adj

        by_item = _index_cost_adj(
            [
                {
                    'i_code': '1001',
                    'v_code': '',
                    'inc_cost': None,
                    'stk_desc': 'خصم 14%',
                    'doc_date': '2026-04-01',
                    'doc_ser': 9,
                }
            ]
        )
        hit = _pick_cost_adj('1001', '2326785', by_item)
        self.assertTrue(hit['cost_adj'])
        self.assertEqual(hit['cost_adj_hint'], 'خصم 14%')
        self.assertEqual(hit['cost_adj_cost_display'], '')


@_with_test_schema
class CostAdjustmentsTests(TestCase):
    @patch('search.oracle_cost_adjustments.oracle_enabled', return_value=True)
    @patch('search.oracle_cost_adjustments._fetch_all')
    def test_shapes_rows_with_account_and_item(self, fetch_all, _enabled):
        from datetime import date, datetime

        from search.oracle_cost_adjustments import fetch_cost_adjustments

        fetch_all.return_value = [
            {
                'DOC_NO': 5501,
                'DOC_SER': 9001,
                'DOC_DATE': datetime(2026, 9, 18, 13, 40),
                'STK_DESC': 'تسوية تكاليف فاتورة شراء',
                'BRN_NO': 6,
                'A_CODE': '21101001',
                'AC_CODE_DTL': '2326785',
                'AC_DTL_TYP': 4,
                'M_V_CODE': None,
                'A_NAME': 'مخزون بضاعة',
                'DTL_NAME': 'مؤسسة الأغذية',
                'I_CODE': '06100',
                'W_CODE': 60,
                'ITM_UNT': 'حبة',
                'P_SIZE': 12,
                'INC_COST': 4.48,
                'WTAVG': 3.9,
                'D_V_CODE': '2326785',
                'I_NAME': 'حليب طازج',
            }
        ]
        report = fetch_cost_adjustments(
            date(2026, 9, 12), date(2026, 9, 18), limit=5
        )
        rows = report['rows']
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['item_code'], '06100')
        self.assertEqual(row['item_name'], 'حليب طازج')
        self.assertEqual(row['account_code'], '21101001')
        self.assertEqual(row['account_name'], 'مخزون بضاعة')
        self.assertEqual(row['acct_code'], '21101001')
        self.assertEqual(row['acct_name'], 'مخزون بضاعة')
        self.assertEqual(row['detail_code'], '2326785')
        self.assertEqual(row['detail_name'], 'مؤسسة الأغذية (2326785)')
        self.assertEqual(row['inc_cost'], 4.48)
        self.assertEqual(row['dtl_type'], 4)
        self.assertEqual(row['vendor_code'], '2326785')
        self.assertEqual(row['vendor_name'], 'مؤسسة الأغذية (2326785)')
        self.assertEqual(row['when'], '2026-09-18')
        self.assertEqual(row['wtavg_display'], '3.9')
        self.assertEqual(row['dtl_type_label'], 'مورد')
        self.assertEqual(report['kpis']['move_count'], 1)
        self.assertEqual(report['kpis']['doc_count'], 1)
        self.assertEqual(report['kpis']['account_count'], 1)
        self.assertEqual(report['kpis']['total_docs_display'], '1')
        self.assertEqual(report['kpis']['total_lines_display'], '1')
        self.assertEqual(report['kpis']['distinct_accounts_display'], '1')
        self.assertEqual(len(report['docs']), 1)
        self.assertEqual(report['docs'][0]['doc_no_display'], '5501')
        self.assertEqual(report['docs'][0]['line_count'], 1)
        self.assertEqual(len(report['docs'][0]['lines']), 1)

    @patch('search.oracle_cost_adjustments.oracle_enabled', return_value=True)
    @patch('search.oracle_cost_adjustments._fetch_all')
    def test_groups_lines_by_document(self, fetch_all, _enabled):
        from datetime import date, datetime

        from search.oracle_cost_adjustments import fetch_cost_adjustments

        base = {
            'DOC_NO': 7568,
            'DOC_SER': 99001,
            'DOC_DATE': datetime(2026, 9, 19, 0, 0),
            'STK_DESC': 'تسوية',
            'BRN_NO': 9,
            'A_CODE': '21101001',
            'AC_CODE_DTL': '230302',
            'AC_DTL_TYP': 4,
            'M_V_CODE': None,
            'A_NAME': 'مخزون',
            'DTL_NAME': 'مورد أ',
            'W_CODE': 902,
            'ITM_UNT': 'حبة',
            'P_SIZE': 1,
            'WTAVG': 10,
            'D_V_CODE': None,
        }
        fetch_all.return_value = [
            {**base, 'I_CODE': 'A1', 'I_NAME': 'صنف 1', 'INC_COST': 5},
            {**base, 'I_CODE': 'A2', 'I_NAME': 'صنف 2', 'INC_COST': 7},
            {
                **base,
                'DOC_NO': 7569,
                'DOC_SER': 99002,
                'I_CODE': 'B1',
                'I_NAME': 'صنف ب',
                'INC_COST': 3,
            },
        ]
        report = fetch_cost_adjustments(date(2026, 9, 19), date(2026, 9, 19), limit=20)
        self.assertEqual(len(report['rows']), 3)
        self.assertEqual(len(report['docs']), 2)
        first = report['docs'][0]
        self.assertEqual(first['doc_no_display'], '7568')
        self.assertEqual(first['line_count'], 2)
        self.assertEqual(first['adj_total'], 12.0)
        self.assertEqual([ln['item_code'] for ln in first['lines']], ['A1', 'A2'])

    def test_party_label_strips_nested_parens_and_uses_vendor_code(self):
        from search.oracle_cost_adjustments import _party_label

        self.assertEqual(
            _party_label(
                'مصنع الجميح لتعبئة المرطبات (300056269810003)',
                '231039',
            ),
            'مصنع الجميح لتعبئة المرطبات (231039)',
        )

    @patch('search.oracle_cost_adjustments.oracle_enabled', return_value=True)
    @patch('search.oracle_cost_adjustments._fetch_all')
    def test_adjust_type_hung_filter_and_binds(self, fetch_all, _enabled):
        from datetime import date

        from search.oracle_cost_adjustments import fetch_cost_adjustments

        fetch_all.return_value = []
        fetch_cost_adjustments(
            date(2026, 9, 12),
            date(2026, 9, 18),
            branch_code='6',
            warehouse_code='60',
            group_code='3',
            item_q='حليب',
            account_q='211',
            limit=10,
        )
        sql, params = fetch_all.call_args[0]
        self.assertEqual(params['adj_type'], 2)
        self.assertEqual(params['brn'], 6)
        self.assertEqual(params['wh'], 60)
        self.assertIn('m.ADJUST_TYPE = :adj_type', sql)
        self.assertIn('(m.HUNG IS NULL OR m.HUNG = 0)', sql)
        self.assertIn('d.DOC_SER = m.DOC_SER', sql)
        self.assertIn('INDX_SER_STK_ADJUSTMENT_DET', sql)
        self.assertIn('i.G_CODE = :gcode', sql)
        self.assertEqual(params['iq'], '%حليب%')
        self.assertEqual(params['aq'], '%211%')

    @patch('search.oracle_cost_adjustments.oracle_enabled', return_value=False)
    def test_raises_when_oracle_disabled(self, _enabled):
        from datetime import date

        from search.oracle_cost_adjustments import fetch_cost_adjustments
        from search.oracle_stock import OracleStockError

        with self.assertRaises(OracleStockError):
            fetch_cost_adjustments(date(2026, 9, 12), date(2026, 9, 18))


class CostAdjustmentsViewTests(TestCase):
    def test_browse_cost_adjustments_url_resolves(self):
        self.assertEqual(
            reverse('browse_cost_adjustments'),
            '/purchases/cost-adjustments/',
        )

    def test_browse_cost_adjustments_view_importable(self):
        from search.views import browse_cost_adjustments

        self.assertTrue(callable(browse_cost_adjustments))

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(reverse('browse_cost_adjustments'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))

    @patch('search.oracle_stock.oracle_enabled', return_value=False)
    def test_authenticated_user_can_open_page_when_oracle_off(self, _enabled):
        user = get_user_model().objects.create_user(
            username='cadj-user',
            password='StrongPassword123!',
        )
        UserProfile.objects.create(
            user=user,
            display_name='تسعيرة',
            phone='0507000001',
            role_name='مدير تسعيرة',
        )
        self.client.force_login(user)
        response = self.client.get(reverse('browse_cost_adjustments'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'تسوية التكاليف')
        self.assertContains(response, 'أوراكل غير مفعّل')

    def test_denied_without_pricing_nav_access(self):
        user = get_user_model().objects.create_user(
            username='purchases-only',
            password='StrongPassword123!',
        )
        UserProfile.objects.create(
            user=user,
            display_name='مشتريات',
            phone='0507000002',
            role_name='مدير مشتريات',
        )
        UserNavPermission.objects.create(
            user=user,
            sections=['sales'],
            blocked_screens=[],
        )
        self.client.force_login(user)
        response = self.client.get(reverse('browse_cost_adjustments'))
        self.assertRedirects(
            response,
            reverse('home'),
            fetch_redirect_response=False,
        )


@_with_test_schema
class TransferRequestCompareTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='trcmp',
            password='StrongPassword123!',
        )
        self.client.force_login(self.user)

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('browse_tr_compare'))
        self.assertEqual(response.status_code, 302)

    @patch('search.oracle_stock.oracle_enabled', return_value=False)
    def test_list_renders_when_oracle_off(self, _enabled):
        response = self.client.get(reverse('browse_tr_compare'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'طلب النواقص')
        self.assertContains(response, 'أوراكل غير مفعّل')

    def test_detail_requires_complete_id(self):
        response = self.client.get(reverse('browse_tr_compare_detail'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'معرّف طلب التحويل غير مكتمل')

    def test_pr_summary_for_short_codes(self):
        from search.oracle_tr_compare import _pr_summary_for_codes

        summary = _pr_summary_for_codes(
            ['A1', 'B2', 'C3'],
            {
                'A1': [{'pr_no': '100', 'pr_type': '1', 'pr_ser': '9'}],
                'C3': [
                    {'pr_no': '100', 'pr_type': '1', 'pr_ser': '9'},
                    {'pr_no': '200', 'pr_type': '1', 'pr_ser': '10'},
                ],
            },
        )
        self.assertEqual(summary['short_pr_item_count'], 2)
        self.assertEqual(summary['short_pr_nos'], ['100', '200'])
        self.assertIn('100', summary['short_pr_display'])
        self.assertIn('A1', summary['short_pr_title'])

    @patch('search.oracle_tr_compare.oracle_enabled', return_value=True)
    @patch('search.oracle_tr_compare._fetch_all')
    def test_fetch_recent_pr_by_items(self, fetch_all, _enabled):
        from datetime import date

        from search.oracle_tr_compare import fetch_recent_pr_by_items

        fetch_all.return_value = [
            {
                'ITEM_CODE': '111',
                'PR_TYPE': 1,
                'PR_NO': 4501,
                'PR_SER': 88,
                'PR_WHEN': date(2026, 9, 1),
                'PR_DAY': '2026-09-01',
            },
            {
                'ITEM_CODE': '111',
                'PR_TYPE': 1,
                'PR_NO': 4501,
                'PR_SER': 88,
                'PR_WHEN': date(2026, 9, 1),
                'PR_DAY': '2026-09-01',
            },
            {
                'ITEM_CODE': '222',
                'PR_TYPE': 1,
                'PR_NO': 4502,
                'PR_SER': 89,
                'PR_WHEN': date(2026, 9, 2),
                'PR_DAY': '2026-09-02',
            },
        ]
        by_item = fetch_recent_pr_by_items(['111', '222', '333'], day=date(2026, 9, 3))
        self.assertEqual(len(by_item['111']), 1)
        self.assertEqual(by_item['111'][0]['pr_no'], '4501')
        self.assertEqual(by_item['222'][0]['pr_no'], '4502')
        self.assertNotIn('333', by_item)


@_with_test_schema
class MainWarehouseForBranchTests(TestCase):
    @patch('search.oracle_tr_compare.oracle_enabled', return_value=True)
    @patch('search.oracle_tr_compare._fetch_all')
    def test_sky_picks_warehouse_60(self, fetch_all, _enabled):
        from search.oracle_tr_compare import fetch_main_warehouse_for_branch

        fetch_all.return_value = [
            {'W_CODE': 15, 'W_NAME': 'مخزن 15', 'MAIN_WCODE': 0},
            {'W_CODE': 60, 'W_NAME': 'مخزن 6 (سكاي مول)', 'MAIN_WCODE': 0},
            {'W_CODE': 62, 'W_NAME': 'مخزن 62', 'MAIN_WCODE': 0},
        ]
        hit = fetch_main_warehouse_for_branch('6')
        self.assertEqual(hit['code'], '60')
        self.assertEqual(hit['name'], 'مخزن 6 (سكاي مول)')

    @patch('search.oracle_tr_compare.oracle_enabled', return_value=True)
    @patch('search.oracle_tr_compare._fetch_all')
    def test_prefers_times_100_when_times_10_missing(self, fetch_all, _enabled):
        from search.oracle_tr_compare import fetch_main_warehouse_for_branch

        fetch_all.return_value = [
            {'W_CODE': 900, 'W_NAME': 'مخزن 9', 'MAIN_WCODE': 0},
            {'W_CODE': 91, 'W_NAME': 'مخزن 91', 'MAIN_WCODE': 0},
        ]
        hit = fetch_main_warehouse_for_branch('9')
        self.assertEqual(hit['code'], '900')


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


class VendorQueryFilterTests(TestCase):
    def test_matches_name_and_code(self):
        from search.oracle_vendor_turnover import apply_vendor_query

        report = {
            'rows': [
                {
                    'vendor_code': '1203',
                    'vendor_name': 'مؤسسة صالح',
                    'decision_key': 'settle',
                    'recv_qty': 10,
                    'sold_qty': 8,
                    'due_amt': 100,
                },
                {
                    'vendor_code': '88',
                    'vendor_name': 'شركة الأغذية',
                    'decision_key': 'hold',
                    'recv_qty': 4,
                    'sold_qty': 1,
                    'due_amt': 20,
                },
            ],
            'kpis': {},
        }
        by_name = apply_vendor_query(report, 'صالح')
        self.assertEqual([r['vendor_code'] for r in by_name['rows']], ['1203'])
        by_code = apply_vendor_query(report, '88')
        self.assertEqual([r['vendor_code'] for r in by_code['rows']], ['88'])
        self.assertEqual(apply_vendor_query(report, 'لا يوجد')['rows'], [])


class PurchaseInvoiceMovementTests(TestCase):
    """حركة فواتير الشراء: توزيع FIFO لصافي البيع على فواتير الصنف في فرعه."""

    @staticmethod
    def _day(n):
        from datetime import date, timedelta

        return date(2026, 9, 1) + timedelta(days=n - 1)

    def _purchase(self, ser, day, qty, cost, *, item='100', brn=6, vendor='V1', name='حليب'):
        return {
            'BILL_SER': ser,
            'BILL_NO': ser + 1000,
            'BILL_DATE': self._day(day),
            'V_CODE': vendor,
            'V_NAME': f'مورد {vendor}',
            'BRN_NO': brn,
            'I_CODE': item,
            'I_NAME': name,
            'G_CODE': 5,
            'QTY': qty,
            'COST': cost,
        }

    def test_older_invoice_is_consumed_first_and_cumulative_carries_over(self):
        from search.oracle_stock_turnover import allocate_fifo

        d = self._day
        result = allocate_fifo(
            [('A', d(1), 10), ('B', d(3), 5)],
            {d(1): 4, d(2): 4, d(3): 5, d(4): 1},
        )
        a, b = result['lines']['A'], result['lines']['B']
        self.assertEqual(a['sold'], 10)
        self.assertEqual(a['sold_out'], d(3))
        self.assertEqual(b['sold'], 4)
        self.assertIsNone(b['sold_out'])
        self.assertEqual((b['cum_qty'], b['cum_sold']), (15, 14))
        self.assertEqual(result['prior'], 0)
        self.assertEqual(result['last_sale'], d(4))

    def test_sales_before_arrival_or_beyond_received_count_as_prior_stock(self):
        from search.oracle_stock_turnover import allocate_fifo

        d = self._day
        early = allocate_fifo([('A', d(5), 10)], {d(3): 6, d(6): 3})
        self.assertEqual(early['lines']['A']['sold'], 3)
        self.assertEqual(early['prior'], 6)

        overflow = allocate_fifo([('A', d(1), 5), ('B', d(2), 5)], {d(1): 8})
        self.assertEqual(overflow['lines']['A']['sold'], 5)
        self.assertEqual(overflow['lines']['B']['sold'], 0)
        self.assertEqual(overflow['prior'], 3)

    def test_pos_return_puts_quantity_back_and_reopens_invoice(self):
        from search.oracle_stock_turnover import allocate_fifo

        d = self._day
        result = allocate_fifo([('A', d(1), 5)], {d(1): 5, d(2): -2})
        self.assertEqual(result['lines']['A']['sold'], 3)
        self.assertIsNone(result['lines']['A']['sold_out'])

    def test_lines_carry_status_value_and_queue(self):
        from search.oracle_stock_turnover import compute_invoice_lines

        d = self._day
        lines = compute_invoice_lines(
            [
                self._purchase(1, 1, 10, 50),
                self._purchase(2, 2, 12, 48),
                self._purchase(3, 1, 8, 40, item='200', name='أرز'),
            ],
            {'100|6': {d(1): 10, d(10): 1}},
            today=d(40),
            branch_names={'6': 'فرع 6'},
        )
        by_key = {line['key']: line for line in lines}
        first, second, dead = by_key['1|100'], by_key['2|100'], by_key['3|200']
        self.assertEqual(first['status'], 'fast_out')
        self.assertEqual(first['sellout_days'], 0)
        self.assertEqual(second['sold'], 1)
        self.assertEqual(second['remaining'], 11)
        self.assertEqual(second['remaining_value'], 44.0)
        self.assertEqual(second['status'], 'stagnant')
        self.assertEqual(second['branch_name'], 'فرع 6')
        self.assertEqual(dead['sold'], 0)
        self.assertEqual(dead['status'], 'stagnant')

    def test_projection_includes_invoices_ahead_in_the_queue(self):
        from search.oracle_stock_turnover import compute_invoice_lines

        d = self._day
        daily = {d(n): 2 for n in range(1, 11)}
        lines = compute_invoice_lines(
            [self._purchase(1, 1, 30, 30), self._purchase(2, 5, 30, 30)],
            {'100|6': daily},
            today=d(10),
        )
        first, second = sorted(lines, key=lambda line: line['bill_ser'])
        self.assertEqual(first['sold'], 20)
        self.assertEqual(first['clear_days'], 5.0)
        self.assertTrue(second['queued'])
        self.assertEqual(second['clear_days'], 20.0)
        self.assertEqual(first['status'], 'fast')
        self.assertEqual(second['status'], 'mid')

    def test_vendor_filter_runs_after_allocation(self):
        from search.oracle_stock_turnover import compute_invoice_lines, filter_lines

        d = self._day
        lines = compute_invoice_lines(
            [self._purchase(1, 1, 5, 5, vendor='A'), self._purchase(2, 2, 5, 5, vendor='B')],
            {'100|6': {d(3): 6}},
            today=d(4),
        )
        only_b = filter_lines(lines, vendor_code='B')
        self.assertEqual([line['sold'] for line in only_b], [1])

    def test_invoice_rollup_takes_status_of_largest_open_value(self):
        from search.oracle_stock_turnover import build_invoice_rows, compute_invoice_lines

        d = self._day
        lines = compute_invoice_lines(
            [
                self._purchase(1, 1, 4, 4, item='100'),
                self._purchase(1, 1, 10, 100, item='200'),
                self._purchase(2, 1, 3, 3, item='300'),
            ],
            {'100|6': {d(2): 4}, '300|6': {d(3): 3}},
            today=d(40),
        )
        rows = {row['bill_ser']: row for row in build_invoice_rows(lines)}
        self.assertEqual(rows['1']['status'], 'stagnant')
        self.assertEqual((rows['1']['line_count'], rows['1']['open_count']), (2, 1))
        self.assertEqual(rows['1']['remaining_value'], 100.0)
        self.assertEqual(rows['2']['status'], 'fast_out')
        self.assertEqual(rows['2']['sellout_days'], 2)

    def test_period_window_depends_on_branch(self):
        from search.oracle_stock import OracleStockError
        from search.oracle_stock_turnover import validate_period

        d = self._day
        with self.assertRaises(OracleStockError):
            validate_period(d(1), d(5), today=d(60), branch_code='')
        self.assertEqual(validate_period(d(1), d(90), today=d(60), branch_code='6'), d(60))
        with self.assertRaises(OracleStockError):
            validate_period(d(1), d(5), today=d(200), branch_code='6')

    def test_sql_leads_by_date_and_joins_pairs_without_exists(self):
        from search.oracle_stock_turnover import _daily_sales_sql, _purchase_lines_sql

        with patch('search.oracle_stock_turnover._schema', return_value='S'), patch(
            'search.oracle_stock_turnover._pos_owner', return_value='P'
        ):
            purchase_sql, purchase_bind = _purchase_lines_sql(branch_code='6', group_code='')
            sale_sql, sale_bind = _daily_sales_sql(kind='pos', branch_code='6', group_code='')
            return_sql, _bind = _daily_sales_sql(kind='pos_return', branch_code='', group_code='')
            bill_sql, _bind = _daily_sales_sql(kind='bill', branch_code='', group_code='5')
            bill_rt_sql, _bind = _daily_sales_sql(kind='bill_return', branch_code='', group_code='')
        self.assertIn('INDX_SER_PI_BILL_DTL', purchase_sql)
        self.assertIn('FREE_QTY', purchase_sql)
        self.assertIsInstance(purchase_bind['brn'], int)
        self.assertIsInstance(sale_bind['brn'], int)
        for sql in (sale_sql, return_sql, bill_sql, bill_rt_sql):
            self.assertIn('p.BRN_NO = m.BRN_NO', sql)
            self.assertIn('>= p.FIRST_DAY', sql)
            self.assertNotIn('EXISTS', sql)
            self.assertNotIn('TO_CHAR(d.I_CODE', sql)
            self.assertNotIn('NVL(m.BILL_SER', sql)
        self.assertIn('IAS_POS_RT_BILL_MST', return_sql)
        self.assertIn('d.BILL_SER = m.BILL_SER', bill_sql)
        self.assertIn('INDX_SER_BILL_DTL', bill_sql)
        self.assertIn('CNCL_FLG', bill_sql)
        self.assertIn('pi.G_CODE = :gcode', bill_sql)
        self.assertIn('d.RT_BILL_SER = m.RT_BILL_SER', bill_rt_sql)

    def test_day_slices_cover_window_without_overlap(self):
        from search.oracle_stock_turnover import day_slices

        d = self._day
        slices = day_slices(d(1), d(31), 4)
        self.assertEqual(len(slices), 4)
        self.assertEqual(slices[0][0], d(1))
        self.assertEqual(slices[-1][1], d(31))
        for (_a, end), (start, _b) in zip(slices, slices[1:]):
            self.assertEqual(end, start)
        self.assertEqual(sum((b - a).days for a, b in slices), 30)
        self.assertEqual(day_slices(d(1), d(3), 4), [(d(1), d(2)), (d(2), d(3))])
        self.assertEqual(day_slices(d(1), d(2), 1), [(d(1), d(2))])

    def test_selling_branch_check_reads_headers_only(self):
        from search.oracle_stock_turnover import _selling_branches_sql

        with patch('search.oracle_stock_turnover._schema', return_value='IAS'), patch(
            'search.oracle_stock_turnover._pos_owner', return_value='YSPOS1'
        ):
            pos_sql = _selling_branches_sql('pos')
            bill_sql = _selling_branches_sql('bill')
        self.assertIn('INDEX(m POSBILLMST_BILLDATEUSRBRN)', pos_sql)
        self.assertNotIn('DTL', pos_sql)
        self.assertNotIn('HUNG', pos_sql)
        self.assertIn('IAS.IAS_BILL_MST', bill_sql)
        self.assertIn('CNCL_FLG', bill_sql)
        self.assertNotIn('DTL', bill_sql)

    def test_invoice_drilldown_matches_invoice_only_not_item_codes(self):
        from search.oracle_stock_turnover import compute_invoice_lines, filter_lines

        d = self._day
        lines = compute_invoice_lines(
            [
                self._purchase(6506, 1, 10, 10, item='121301'),
                self._purchase(37171, 2, 10, 10, item='10007025622445765061'),
            ],
            {},
            today=d(5),
        )
        by_query = filter_lines(lines, item_query='6506')
        self.assertEqual([line['bill_ser'] for line in by_query], ['37171'])
        by_bill = filter_lines(lines, bill_ser='6506')
        self.assertEqual([line['item_code'] for line in by_bill], ['121301'])

    def test_branch_without_any_sales_is_not_called_slow_or_stagnant(self):
        from search.oracle_stock_turnover import compute_invoice_lines

        d = self._day
        lines = compute_invoice_lines(
            [self._purchase(1, 1, 10, 10, brn=2), self._purchase(2, 1, 10, 10, brn=6)],
            {},
            today=d(40),
            selling_branches={'6'},
        )
        by_branch = {line['branch_code']: line['status'] for line in lines}
        self.assertEqual(by_branch, {'2': 'no_branch_sales', '6': 'stagnant'})

    @patch('search.oracle_stock_turnover._attach_item_barcodes')
    @patch('search.oracle_stock_turnover.oracle_enabled', return_value=True)
    def test_report_pages_on_server_and_ignores_unknown_vendor(self, _enabled, _barcodes):
        from search.oracle_stock_turnover import build_stock_turnover_report, compute_invoice_lines

        d = self._day
        lines = compute_invoice_lines(
            [self._purchase(n, 1, 5, 10, item=str(500 + n)) for n in range(1, 121)],
            {},
            today=d(10),
        )
        with patch('search.oracle_stock_turnover.load_invoice_lines', return_value=lines):
            report = build_stock_turnover_report(
                d(1), d(10), view='line', page=3, vendor_code='nope', today=d(10)
            )
            waiting = build_stock_turnover_report(
                d(1), d(10), view='invoice', status='stagnant', today=d(10)
            )
        self.assertEqual((report['total'], report['page_count']), (120, 3))
        self.assertEqual((len(report['rows']), report['page_start']), (20, 100))
        self.assertEqual(report['vendor'], '')
        self.assertEqual(report['kpis']['invoice_count'], 120)
        self.assertEqual(report['status_counts'][0]['code'], 'waiting')
        self.assertEqual(waiting['total'], 0)

    def test_anonymous_user_is_redirected(self):
        response = self.client.get(reverse('browse_stock_turnover'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))

    @patch('search.oracle_stock.oracle_enabled', return_value=False)
    def test_page_opens_when_oracle_off(self, _enabled):
        user = get_user_model().objects.create_user(username='inv-user', password='StrongPassword123!')
        UserProfile.objects.create(user=user, display_name='مخازن', phone='0507000011', role_name='مدير مخازن')
        self.client.force_login(user)
        response = self.client.get(reverse('browse_stock_turnover'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'حركة فواتير الشراء')
        self.assertContains(response, 'أوراكل غير مفعّل')

    def test_denied_without_inventory_nav_access(self):
        user = get_user_model().objects.create_user(username='pur-only', password='StrongPassword123!')
        UserProfile.objects.create(user=user, display_name='مشتريات', phone='0507000012', role_name='مدير مشتريات')
        UserNavPermission.objects.create(user=user, sections=['purchases'], blocked_screens=[])
        self.client.force_login(user)
        response = self.client.get(reverse('browse_stock_turnover'))
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)


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
        from search.oracle_stock import use_oracle_stock

        with override_settings(STOCK_QTY_SOURCE='api'):
            self.assertEqual(stock_qty_source(), 'api')
            self.client.force_login(self.admin)
            self._post_fetch()
            self.assertEqual(stock_qty_source(), 'oracle')
            self.assertTrue(use_oracle_stock())
            self._post_fetch(enabled=None, password='')
            self.assertEqual(stock_qty_source(), 'api')
            self.assertFalse(use_oracle_stock())

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
        from search.views import _compare_warehouse_codes

        with override_settings(EXTERNAL_API={'BASE_URL': '', 'DEFAULT_WAREHOUSE': '', 'COMPARE_WAREHOUSES': []}):
            self.assertEqual(default_warehouse(), '')
            self.assertEqual(_compare_warehouse_codes([]), [])
            self.client.force_login(self.admin)
            self._post_fetch(default_warehouse='', compare_warehouses='701, 30')
            self.assertEqual(default_warehouse(), '701')
            self._post_fetch(password='', default_warehouse='30', compare_warehouses='')
            self.assertEqual(default_warehouse(), '30')
            self.assertEqual(_compare_warehouse_codes([{'code': '30'}, {'code': '60'}]), ['30'])


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
    @patch('search.home_dashboard.build_stagnant_widget')
    def test_sales_manager_cannot_read_other_sections_directly(self, stagnant, turnover, inventory, purchases):
        self.client.force_login(self.sales_user)
        for name in ('purchases', 'inventory', 'turnover', 'shortage', 'stagnant'):
            response = self.client.get(self._url(name))
            self.assertEqual(response.status_code, 403, name)
        for mock in (stagnant, turnover, inventory, purchases):
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
        for name in ('sales', 'purchases', 'inventory', 'profit', 'turnover', 'shortage', 'stagnant', 'recent', 'trend'):
            self.assertIn(f'data-hx-card="{name}"', html, name)

    def test_user_without_sections_keeps_welcome_screen(self):
        self.client.force_login(self.none_user)
        html = self.client.get(reverse('home')).content.decode()
        self.assertNotIn('data-hx', html)
        self.assertIn('welcome-hero', html)


