import json
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import DeleteView, ListView
from django.views.generic.base import TemplateView

from django_finance.apps.plaid.models import Account, Category, Item, PlaidLinkEvent, Transaction
from django_finance.apps.plaid.tasks import update_transactions
from django_finance.apps.plaid.utils import plaid_config
from django_finance.apps.plaid.webhooks import (
    handle_item_webhook,
    handle_transactions_webhook,
    verify_webhook,
)
from plaid.model.item_public_token_exchange_request import (
    ItemPublicTokenExchangeRequest,
)
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_update import LinkTokenCreateRequestUpdate
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.sandbox_item_fire_webhook_request import SandboxItemFireWebhookRequest
from plaid.model.sandbox_item_reset_login_request import SandboxItemResetLoginRequest

from django.db.models import Sum, Q
from django.utils import timezone
from datetime import timedelta, date
from decimal import Decimal

logger = logging.getLogger(__name__)


class DashboardView(LoginRequiredMixin, TemplateView):
    """
    Gives a user information about integrated banks, accounts, transaction info etc.
    """

    template_name = "plaid/index.html"

    def get_context_data(self, *args, **kwargs) -> dict:
        context = super(DashboardView, self).get_context_data()
        user = self.request.user
        items = Item.objects.filter(user=user)
        name_of_banks_connected = items.values_list("institution_name", flat=True)

        # Net worth across all banks
        net_worth = Account.objects.filter(item__user=user).aggregate(Sum("current_balance"))["current_balance__sum"]

        # Total income and total expense
        total_income = Transaction.objects.filter(account__item__user=user, amount__gt=0).aggregate(Sum("amount"))[
            "amount__sum"
        ]
        total_expense = Transaction.objects.filter(account__item__user=user, amount__lt=0).aggregate(Sum("amount"))[
            "amount__sum"
        ]

        # 5 Recent transactions
        transactions = Transaction.objects.filter(account__item__user=user)[:5]

        # Category spending breakdown
        category_spending = (
            Transaction.objects.filter(account__item__user=user)
            .values("primary_personal_finance_category")
            .distinct()
            .order_by()
            .annotate(total_spending=Sum("amount"))
        )

        category_spending_json = json.dumps(list(category_spending), cls=DjangoJSONEncoder)  # For chart.js

        context = {
            "items": items,
            "no_of_banks": items.count(),
            "name_of_banks_connected": name_of_banks_connected,
            "net_worth": net_worth,
            "total_income": total_income,
            "total_expense": total_expense,
            "transactions": transactions,
            "category_spending": category_spending,
            "category_spending_json": category_spending_json,
        }

        return context


class AccountsInItemView(LoginRequiredMixin, ListView):
    model = Account
    context_object_name = "accounts"
    template_name = "components/account_list.html"

    def get_queryset(self):
        item_id = self.kwargs.get("pk")
        return Account.objects.filter(item_id=item_id)


class CreatePlaidLinkToken(LoginRequiredMixin, View):
    """
    Create a link_token and pass the temporary token to your app's client.
    https://plaid.com/docs/api/tokens/#linktokencreate
    """

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(self.request.body)
            plaid_id = data.get("plaid_id")
            new_accounts_detected = data.get("new_accounts_detected")

            link_request = LinkTokenCreateRequest(
                products=plaid_config.products,
                client_name=plaid_config.client_name,
                country_codes=plaid_config.country_codes,
                language=plaid_config.language,
                user=LinkTokenCreateRequestUser(client_user_id=str(self.request.user.id)),
                redirect_uri=plaid_config.redirect_uri,
                webhook=plaid_config.webhook_uri,
            )

            # In link update mode, include access token and an empty products array
            if plaid_id:
                link_request["access_token"] = Item.objects.filter(id=plaid_id).first().access_token
                link_request["products"] = []

            # Request new accounts in update mode
            if new_accounts_detected:
                link_request["update"] = LinkTokenCreateRequestUpdate(account_selection_enabled=True)

            link_response = plaid_config.client.link_token_create(link_request)
            return JsonResponse(link_response.to_dict(), status=201)
        except Exception as e:
            logger.error(
                f"Something went wrong in CreateLinkToken for user {self.request.user} -> {str(e)}",
            )
            return JsonResponse({}, status=500)


class ExchangePlaidPublicAccessToken(LoginRequiredMixin, View):
    """
    Exchange the public_token for a permanent access_token and item_id.
    https://plaid.com/docs/api/items/#itempublic_tokenexchange
    """

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(self.request.body)
            public_token = data.get("public_token")
            institution_id = data.get("institution_id")
            name = data.get("institution_name")

            items = Item.objects.filter(user=self.request.user)

            if items.filter(institution_id=institution_id).exists():
                messages.error(request, "You have already linked an item at this institution.")
                return render(request, "components/bank_cards.html", {"items": items})

            exchange_request = ItemPublicTokenExchangeRequest(public_token=public_token)
            exchange_response = plaid_config.client.item_public_token_exchange(exchange_request)

            access_token = exchange_response.get("access_token")
            item_id = exchange_response.get("item_id")

            instance = Item.objects.create(
                user=self.request.user,
                access_token=access_token,
                item_id=item_id,
                institution_id=institution_id,
                institution_name=name,
                status=Item.ItemStatusChoices.GOOD,
            )

            # Make an initial call to fetch transactions
            update_transactions.delay(instance.id)

            items = Item.objects.filter(user=self.request.user)
            return render(request, "components/bank_cards.html", {"items": items})
        except Exception as e:
            logger.error(f"Something went wrong in ExchangePublicAccessToken for user {self.request.user} -> {str(e)}")
            messages.error(request, "Something went wrong while integrating your bank account.")
            items = Item.objects.filter(user=self.request.user)
            return render(request, "components/bank_cards.html", {"items": items})


class PlaidRemoveItemView(LoginRequiredMixin, DeleteView):
    """
    Deletes a plaid item from Plaid and the database.
    """

    model = Item
    context_object_name = "item"
    template_name = "plaid/remove_item.html"
    success_url = reverse_lazy("dashboard")

    def delete(self, *args, **kwargs):
        self.object = self.get_object()

        try:
            access_token = self.object.access_token
            remove_request = ItemRemoveRequest(access_token=access_token)
            plaid_config.client.item_remove(remove_request)
        except Exception as e:
            logger.error(f"Something went wrong in removing plaid item with id {self.object.id} , error: {str(e)}")
            messages.error(
                self.request,
                "Item could not be removed in Plaid. Please try again.",
            )
        else:
            self.object.delete()
            logger.info("Plaid item deleted successfully")
        finally:
            items = Item.objects.filter(user=self.request.user)
            return render(self.request, "components/bank_cards.html", {"items": items})


class CreatePlaidLinkEvent(LoginRequiredMixin, View):
    """
    Create link events from link creation for troubleshooting purpose.
    """

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(self.request.body)
            link_session_id = data.get("link_session_id")
            event_type = data.get("event_type")
            request_id = data.get("request_id")
            error_type = data.get("error_type")
            error_code = data.get("error_code")

            values = {
                "user_id": request.user.id,
                "event_type": event_type,
                "link_session_id": link_session_id,
            }

            if request_id:
                values["request_id"] = request_id

            if error_type:
                values["error_type"] = error_type

            if error_code:
                values["error_code"] = error_code

            PlaidLinkEvent.objects.create(**values)

            return HttpResponse(status=201)

        except Exception as e:
            logger.error(f"Something went wrong in CreatePlaidLinkEvent {request.user}, error: {str(e)}")
            return HttpResponse(status=500)


class UpdatePlaidItemStatus(LoginRequiredMixin, View):
    """
    Update item's status to good and new_accounts_detected to False.
    """

    def post(self, request, *args, **kwargs):
        try:
            plaid_id = json.loads(self.request.body).get("plaid_id")
            Item.objects.filter(id=plaid_id).update(status=Item.ItemStatusChoices.GOOD, new_accounts_detected=False)
            return JsonResponse(
                {
                    "msg": "Item updated successfully.",
                    "alert": "success",
                },
                status=200,
            )
        except Exception as e:
            logger.error(f"Something went wrong in UpdatePlaidItemStatus {request.user} , error: {str(e)}")
            return JsonResponse(
                {
                    "msg": "Something went wrong while updating your bank account.",
                    "alert": "danger",
                },
                status=500,
            )


class PlaidSandboxItemResetLogin(LoginRequiredMixin, View):
    """
    Forces an Item into an ITEM_LOGIN_REQUIRED state in order to simulate an Item whose login is no longer valid.
    """

    def post(self, request, *args, **kwargs):
        try:
            access_token = Item.objects.first().access_token
            reset_login_request = SandboxItemResetLoginRequest(access_token)
            plaid_config.client.sandbox_item_reset_login(reset_login_request)
            return HttpResponse(status=200)
        except Exception as e:
            logger.error(f"Something went wrong in PlaidSandboxItemResetLogin {request.user} , error: {str(e)}")
            return HttpResponse(status=500)


class PlaidSandboxItemFireWebhook(LoginRequiredMixin, View):
    """
    Sandbox endpoint used to test that code correctly handles webhooks.
    """

    def post(self, request, *args, **kwargs):
        try:
            access_token = Item.objects.first().access_token
            reset_login_request = SandboxItemFireWebhookRequest(access_token, webhook_code="NEW_ACCOUNTS_AVAILABLE")
            plaid_config.client.sandbox_item_fire_webhook(reset_login_request)
            return HttpResponse(status=200)
        except Exception as e:
            logger.error(f"Something went wrong in PlaidSandboxItemFireWebhook {request.user} , error: {str(e)}")
            return HttpResponse(status=500)

class SavingsView(LoginRequiredMixin, TemplateView):
    template_name = "plaid/savings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Get savings accounts (depository + savings subtype)
        savings_accounts = Account.objects.filter(
            item__user=user,
            account_type='depository',
            # You can adjust this filter based on your Plaid data
            account_subtype__in=['savings']
        ).select_related('item')

        total_savings_balance = savings_accounts.aggregate(
            total=Sum('current_balance')
        )['total'] or Decimal('0.00')

        # MTD and YTD savings (deposits)
        today = timezone.now().date()
        start_of_month = today.replace(day=1)
        start_of_year = today.replace(month=1, day=1)

        mtd_savings = Transaction.objects.filter(
            account__in=savings_accounts,
            date__gte=start_of_month,
            amount__gt=0
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        ytd_savings = Transaction.objects.filter(
            account__in=savings_accounts,
            date__gte=start_of_year,
            amount__gt=0
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        recent_savings_tx = Transaction.objects.filter(
            account__in=savings_accounts
        ).order_by('-date')[:10]

        context.update({
            'savings_accounts': savings_accounts,
            'total_savings_balance': total_savings_balance,
            'mtd_savings': mtd_savings,
            'ytd_savings': ytd_savings,
            'recent_savings_tx': recent_savings_tx,
            'savings_goals': [],          # ← No model yet
            'to_goal': Decimal('0.00'),
        })
        return context
    
class InvestmentsView(LoginRequiredMixin, TemplateView):
    template_name = "plaid/investments.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        investment_accounts = Account.objects.filter(
            item__user=user,
            account_type='investment'   # This catches 401k, Roth, HSA, IRA, brokerage, etc.
        ).select_related('item')

        total_invested = investment_accounts.aggregate(
            total=Sum('current_balance')
        )['total'] or Decimal('0.00')

        # You can later add holdings data via Plaid's /investments/holdings/get
        context.update({
            'investment_accounts': investment_accounts,
            'total_invested': total_invested,
            # growth, allocation, etc.
        })
        return context
    
class LoansView(LoginRequiredMixin, TemplateView):
    template_name = "plaid/loans.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # Loans + Credit Cards (common debt accounts)
        loan_accounts = Account.objects.filter(
            item__user=user,
            account_type__in=['loan', 'credit']
        ).select_related('item')

        total_debt = loan_accounts.aggregate(
            total=Sum('current_balance')
        )['total'] or Decimal('0.00')

        # Separate credit cards vs other loans if you want
        credit_cards = loan_accounts.filter(account_type='credit')
        other_loans = loan_accounts.filter(account_type='loan')

        total_credit_card_debt = credit_cards.aggregate(
            total=Sum('current_balance')
        )['total'] or Decimal('0.00')

        total_other_loans = other_loans.aggregate(
            total=Sum('current_balance')
        )['total'] or Decimal('0.00')

        context.update({
            'loan_accounts': loan_accounts,
            'credit_cards': credit_cards,
            'other_loans': other_loans,
            'total_debt': total_debt,
            'total_credit_card_debt': total_credit_card_debt,
            'total_other_loans': total_other_loans,
            'recent_payments': Transaction.objects.filter(
                account__in=loan_accounts
            ).order_by('-date')[:10],
        })
        return context
    
class BudgetView(LoginRequiredMixin, TemplateView):
    template_name = "plaid/budget.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        today = timezone.now().date()

        # Non-investment accounts for spending
        spending_accounts = Account.objects.filter(
            item__user=user,
            account_type__in=['depository', 'credit']
        )

        start_of_month = today.replace(day=1)
        start_of_year = today.replace(month=1, day=1)

        categories = Category.objects.filter(user=user).order_by('group', 'name')

        budget_data = []
        total_budgeted = Decimal('0')
        total_spent_mtd = Decimal('0')

        for cat in categories:
            # MTD spending (negative amounts = outflows)
            mtd_spent = Transaction.objects.filter(
                account__in=spending_accounts,
                category=cat,
                date__gte=start_of_month,
                amount__lt=0
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            ytd_spent = Transaction.objects.filter(
                account__in=spending_accounts,
                category=cat,
                date__gte=start_of_year,
                amount__lt=0
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            remaining = cat.budgeted_amount + mtd_spent   # mtd_spent is negative

            percent_used = (abs(mtd_spent) / cat.budgeted_amount * 100) if cat.budgeted_amount > 0 else 0

            budget_data.append({
                'category': cat,
                'mtd_spent': -mtd_spent,          # show as positive number
                'ytd_spent': -ytd_spent,
                'budgeted': cat.budgeted_amount,
                'remaining': remaining,
                'percent_used': min(percent_used, 100),
            })

            total_budgeted += cat.budgeted_amount
            total_spent_mtd += -mtd_spent

        context.update({
            'budget_data': budget_data,
            'total_budgeted': total_budgeted,
            'total_spent_mtd': total_spent_mtd,
            'remaining_budget': total_budgeted + total_spent_mtd,
            'categories': categories,
        })
        return context


class TransactionsView(LoginRequiredMixin, TemplateView):
    template_name = "plaid/transactions.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        transactions = Transaction.objects.filter(
            account__item__user=user,
            account__account_type__in=['depository', 'credit']  # exclude investments
        ).select_related('account', 'category', 'account__item').order_by('-date')

        context.update({
            'transactions': transactions,
            'categories': Category.objects.filter(user=user).order_by('name'),
        })
        return context

@method_decorator(csrf_exempt, name="dispatch")
class PlaidWebhook(View):
    """
    Handles Plaid webhooks.
    """

    def post(self, request, *args, **kwargs):
        try:
            if verify_webhook(request.body, request.headers):
                payload = json.loads(request.body)
                webhook_type = payload.get("webhook_type")
                webhook_code = payload.get("webhook_code")
                item_id = payload.get("item_id")
                error = payload.get("error")

                if webhook_type == "ITEM":
                    handle_item_webhook(webhook_code, item_id, error)

                elif webhook_type == "TRANSACTIONS":
                    handle_transactions_webhook(webhook_code, item_id)

                return HttpResponse(status=200)

            else:
                logger.info("Webhook didn't pass verification")
                return HttpResponse(status=401)

        except Exception as e:
            logger.error(f"Webhook error {str(e)}")
            return HttpResponse(status=500)
