from django.urls import path
from django.views.generic import RedirectView

from . import views, views_users

urlpatterns = [
    path('', views.home, name='home'),
    path(
        'home/api/widget/<slug:name>/',
        views.home_widget_api,
        name='home_widget_api',
    ),
    path('inventory/', views.browse_inventory, name='browse_inventory'),
    path('purchases/', views.browse_purchases, name='browse_purchases'),
    path('sales/', views.browse_sales, name='browse_sales'),
    path(
        'sales/api/groups/',
        views.browse_sales_groups_api,
        name='browse_sales_groups_api',
    ),
    path(
        'sales/api/groups-month/',
        views.browse_sales_groups_month_api,
        name='browse_sales_groups_month_api',
    ),
    path(
        'sales/api/top-items/',
        views.browse_sales_top_items_api,
        name='browse_sales_top_items_api',
    ),
    path(
        'sales/api/top-users/',
        views.browse_sales_top_users_api,
        name='browse_sales_top_users_api',
    ),
    path('profitability/', views.browse_profitability, name='browse_profitability'),
    path('hr/', views.browse_hr, name='browse_hr'),
    # «تحليل الأداء» استُبدل بالربحية: الروابط القديمة تنتقل للشاشة الجديدة
    path('sales/performance/', RedirectView.as_view(pattern_name='browse_profitability', permanent=False)),
    path('users/', views_users.user_list, name='user_list'),
    path('users/new/', views_users.user_create, name='user_create'),
    path('users/activity/', views_users.user_activity, name='user_activity'),
    path('users/<int:pk>/', views_users.user_edit, name='user_edit'),
    path('users/<int:pk>/delete/', views_users.user_delete, name='user_delete'),
    path(
        'settings/oracle/',
        views.oracle_connection_settings,
        name='oracle_connection_settings',
    ),
]
