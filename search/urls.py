from django.urls import path

from . import views

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
    path('sales/performance/', views.browse_performance, name='browse_performance'),
    path(
        'settings/oracle/',
        views.oracle_connection_settings,
        name='oracle_connection_settings',
    ),
]
