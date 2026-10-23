"""شاشة المستخدمين: القائمة والإضافة والتعديل والحذف — لأدوار الإدارة (is_staff) فقط."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import AppUserForm
from .nav_permissions import (
    ROLE_DETAILS,
    assignable_roles,
    can_manage_user,
    user_role_name,
)

User = get_user_model()


def _staff_only(view):
    """إدارة المستخدمين لمدير النظام والسوبر أدمن فقط."""

    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            messages.error(request, 'إدارة المستخدمين متاحة لمدير النظام فقط.')
            return redirect('home')
        return view(request, *args, **kwargs)

    wrapper.__name__ = view.__name__
    wrapper.__doc__ = view.__doc__
    return wrapper


def _visible_users(actor):
    """حسابات السوبر أدمن لا يراها إلا سوبر أدمن — مدير النظام لا يعرف بوجودها."""
    qs = User.objects.select_related('profile')
    if not actor.is_superuser:
        qs = qs.filter(is_superuser=False)
    return qs


def _role_cards(users: list, actor) -> list[dict]:
    counts: dict[str, int] = {}
    for u in users:
        counts[u.role] = counts.get(u.role, 0) + 1
    return [
        {
            'name': role,
            'count': counts.get(role, 0),
            **ROLE_DETAILS[role],
        }
        for role, _label in assignable_roles(actor)
    ]


def _role_options(form) -> list[dict]:
    """أزرار اختيار الدور مع وصف ما يفتحه كل دور."""
    return [
        {'radio': radio, 'details': ROLE_DETAILS.get(radio.data['value'])}
        for radio in form['role_name']
    ]


def _decorate(user, actor) -> None:
    """يضيف للمستخدم ما يحتاجه الجدول: الاسم والرقم والدور وصلاحية التعديل."""
    profile = getattr(user, 'profile', None)
    user.display = ((profile.display_name if profile else '') or user.first_name or user.username).strip()
    user.phone_display = ((profile.phone if profile else '') or '').strip()
    user.role = user_role_name(user) or 'بلا دور'
    details = ROLE_DETAILS.get(user.role)
    user.role_tone = details['tone'] if details else 'legacy'
    user.role_screens = details['screens'] if details else ()
    user.can_manage = can_manage_user(actor, user)
    user.is_self = user.pk == actor.pk
    user.initial = user.display[:1]


@login_required
@never_cache
@_staff_only
def user_list(request):
    """قائمة المستخدمين مع بطاقة لكل دور."""
    users = list(_visible_users(request.user).order_by('-is_active', '-is_superuser', '-is_staff', 'first_name'))
    for u in users:
        _decorate(u, request.user)
    query = (request.GET.get('q') or '').strip()
    role_filter = (request.GET.get('role') or '').strip()
    shown = users
    if role_filter:
        shown = [u for u in shown if u.role == role_filter]
    if query:
        q = query.lower()
        shown = [u for u in shown if q in u.display.lower() or q in u.phone_display]
    return render(
        request,
        'search/users_list.html',
        {
            'users': shown,
            'total': len(users),
            'active_count': sum(1 for u in users if u.is_active),
            'role_cards': _role_cards(users, request.user),
            'query': query,
            'role_filter': role_filter,
        },
    )


@login_required
@never_cache
@_staff_only
@require_http_methods(['GET', 'POST'])
def user_create(request):
    form = AppUserForm(request.POST or None, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        messages.success(request, f'أُضيف المستخدم «{user.first_name}».')
        return redirect('user_list')
    return render(
        request,
        'search/user_form.html',
        {'form': form, 'is_new': True, 'role_options': _role_options(form)},
    )


@login_required
@never_cache
@_staff_only
@require_http_methods(['GET', 'POST'])
def user_edit(request, pk: int):
    target = get_object_or_404(_visible_users(request.user), pk=pk)
    if not can_manage_user(request.user, target):
        return HttpResponseForbidden('لا تملك صلاحية تعديل هذا الحساب.')
    form = AppUserForm(request.POST or None, instance=target, actor=request.user)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        if user.pk == request.user.pk and form.cleaned_data.get('password'):
            # تغيير كلمة سر الحساب الحالي لا يُخرج صاحبه
            update_session_auth_hash(request, user)
        messages.success(request, f'حُفظت بيانات «{user.first_name}».')
        return redirect('user_list')
    _decorate(target, request.user)
    return render(
        request,
        'search/user_form.html',
        {'form': form, 'is_new': False, 'target': target, 'role_options': _role_options(form)},
    )


@login_required
@never_cache
@_staff_only
@require_http_methods(['GET', 'POST'])
def user_delete(request, pk: int):
    target = get_object_or_404(_visible_users(request.user), pk=pk)
    if not can_manage_user(request.user, target):
        return HttpResponseForbidden('لا تملك صلاحية حذف هذا الحساب.')
    _decorate(target, request.user)
    error = ''
    if target.pk == request.user.pk:
        error = 'لا يمكنك حذف حسابك.'
    elif target.is_superuser and not User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target.pk).exists():
        error = 'هذا آخر سوبر أدمن مفعّل؛ لا يمكن حذفه.'
    if request.method == 'POST' and not error:
        name = target.display
        target.delete()
        messages.success(request, f'حُذف المستخدم «{name}».')
        return redirect('user_list')
    return render(request, 'search/user_confirm_delete.html', {'target': target, 'error': error})
