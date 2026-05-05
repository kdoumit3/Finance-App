from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

# ====================== DASHBOARD ======================
@login_required
def dashboard(request):
    context = {
        'net_worth': 142890,
        'net_worth_change': '+2,450',
        'mtd_income': 8750,
        'mtd_expenses': 6300,
        'savings_rate': 28,
    }
    return render(request, 'dashboard.html', context)   # ← Simplified


# ====================== PLACEHOLDERS ======================
@login_required
def accounts_list(request):
    return render(request, 'accounts.html', {'title': 'Accounts'})

@login_required
def transactions_list(request):
    return render(request, 'transactions.html', {'title': 'Transactions'})

@login_required
def budgeting(request):
    return render(request, 'budgeting.html', {'title': 'Budgeting'})

@login_required
def categories(request):
    return render(request, 'categories.html', {'title': 'Categories'})

@login_required
def forecasting(request):
    return render(request, 'forecasting.html', {'title': 'Forecasting'})

@login_required
def calendar_view(request):
    return render(request, 'calendar.html', {'title': 'Calendar'})

@login_required
def savings_goals(request):
    return render(request, 'savings_goals.html', {'title': 'Savings Goals'})

@login_required
def income_goals(request):
    return render(request, 'income_goals.html', {'title': 'Income Goals'})

@login_required
def investments(request):
    return render(request, 'investments.html', {'title': 'Investments'})

@login_required
def retirement(request):
    return render(request, 'retirement.html', {'title': 'Retirement'})

@login_required
def hsa(request):
    return render(request, 'hsa.html', {'title': 'HSA'})

@login_required
def loans(request):
    return render(request, 'loans.html', {'title': 'Loans'})

@login_required
def plaid_sync(request):
    return JsonResponse({'status': 'success', 'message': 'Sync started in background'})