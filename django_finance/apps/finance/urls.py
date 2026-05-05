from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    # Money
    path("accounts/", views.accounts_list, name="accounts"),
    path("transactions/", views.transactions_list, name="transactions"),
    path("budgeting/", views.budgeting, name="budgeting"),
    path("categories/", views.categories, name="categories"),

    # Planning
    path("forecasting/", views.forecasting, name="forecasting"),
    path("calendar/", views.calendar_view, name="calendar"),

    # Goals
    path("savings-goals/", views.savings_goals, name="savings_goals"),
    path("income-goals/", views.income_goals, name="income_goals"),
    path("investments/", views.investments, name="investments"),
    path("retirement/", views.retirement, name="retirement"),
    path("hsa/", views.hsa, name="hsa"),

    # Debt
    path("loans/", views.loans, name="loans"),

    # Plaid
    path("sync/", views.plaid_sync, name="plaid_sync"),
]