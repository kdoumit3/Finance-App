from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from django_finance.apps.common.models import BaseModel


class Item(BaseModel):
    """Plaid Item (login to a financial institution)"""

    class ItemStatusChoices(models.TextChoices):
        GOOD = "GOOD", _("Good")
        BAD = "BAD", _("Bad")

    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="items", on_delete=models.CASCADE)
    access_token = models.CharField(unique=True, max_length=255)
    item_id = models.CharField(unique=True, max_length=255)
    institution_id = models.TextField()
    institution_name = models.TextField()
    status = models.CharField(max_length=4, choices=ItemStatusChoices)
    new_accounts_detected = models.BooleanField(default=False)
    transactions_cursor = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.institution_name} - {self.user}"


class PlaidLinkEvent(BaseModel):
    """Log of Plaid Link events"""

    class EventTypeChoices(models.TextChoices):
        SUCCESS = "SUCCESS", _("Success")
        EXIT = "EXIT", _("Exit")

    user_id = models.CharField(max_length=250)
    event_type = models.CharField(max_length=20, choices=EventTypeChoices)
    link_session_id = models.TextField()
    request_id = models.TextField(blank=True)
    error_type = models.TextField(blank=True)
    error_code = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"LinkEvent: user_id={self.user_id}, type={self.event_type}"


class Account(BaseModel):
    """Financial Account from Plaid"""

    class ACCOUNT_TYPE_CHOICES(models.TextChoices):
        INVESTMENT = "investment", _("Investment")
        CREDIT = "credit", _("Credit")
        DEPOSITORY = "depository", _("Depository")
        LOAN = "loan", _("Loan")
        BROKERAGE = "brokerage", _("Brokerage")
        OTHER = "other", _("Other")

    item = models.ForeignKey(Item, related_name="accounts", on_delete=models.CASCADE)
    account_id = models.CharField(unique=True, max_length=255)
    name = models.CharField(max_length=250)
    mask = models.CharField(max_length=4, blank=True)
    official_name = models.TextField(blank=True)
    available_balance = models.DecimalField(max_digits=65, decimal_places=30, blank=True, null=True)
    current_balance = models.DecimalField(max_digits=65, decimal_places=30, blank=True, null=True)
    limit = models.DecimalField(max_digits=65, decimal_places=30, blank=True, null=True)
    iso_currency_code = models.CharField(max_length=100, blank=True)
    unofficial_currency_code = models.CharField(max_length=100, blank=True)
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES)
    account_subtype = models.CharField(max_length=250, blank=True)

    def __str__(self):
        return f"{self.name} ({self.account_type})"


class Category(BaseModel):
    """User-defined budget categories"""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=100, help_text="e.g. Groceries, Rent, Salary")
    group = models.CharField(max_length=50, blank=True, help_text="e.g. Food, Housing, Income")
    is_income = models.BooleanField(default=False)
    budgeted_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        unique_together = ('user', 'name')
        ordering = ('group', 'name')

    def __str__(self):
        return f"{self.name} ({self.group or 'No Group'})"


class Transaction(BaseModel):
    """Plaid Transaction with manual budgeting fields"""
    
    class TRANSACTION_CATEGORY_CONFIDENCE_LEVEL(models.TextChoices):
        UNKNOWN = "unknown", _("Unknown")
        LOW = "low", _("Low")
        MEDIUM = "medium", _("Medium")
        HIGH = "high", _("High")
        VERY_HIGH = "very_high", _("Very High")

    account = models.ForeignKey(Account, related_name="transactions", on_delete=models.CASCADE)
    transaction_id = models.CharField(unique=True, max_length=255)
    amount = models.DecimalField(max_digits=65, decimal_places=30, blank=True, null=True)
    iso_currency_code = models.CharField(max_length=100, blank=True)
    unofficial_currency_code = models.CharField(max_length=100, blank=True)
    check_number = models.TextField(blank=True)
    location = models.JSONField(blank=True, null=True)
    name = models.TextField(blank=True)
    merchant_name = models.TextField(blank=True)
    merchant_entity_id = models.TextField(blank=True)
    pending = models.BooleanField(default=False)
    account_owner = models.TextField(blank=True)
    logo_url = models.URLField(blank=True, null=True)
    website = models.TextField(blank=True)
    date = models.DateField()
    authorized_date = models.DateField(blank=True, null=True)
    datetime = models.DateTimeField(blank=True, null=True)
    authorized_datetime = models.DateTimeField(blank=True, null=True)
    primary_personal_finance_category = models.TextField(blank=True)
    detailed_personal_finance_category = models.TextField(blank=True)
    confidence_level = models.CharField(max_length=200, blank=True, choices=TRANSACTION_CATEGORY_CONFIDENCE_LEVEL)
    personal_finance_category_icon_url = models.URLField(blank=True, null=True)

    # Manual budgeting fields
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transactions'
    )
    notes = models.TextField(blank=True)

    # For transaction splitting (future feature)
    is_split = models.BooleanField(default=False)
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='splits'
    )

    class Meta:
        ordering = ("-date",)

    def __str__(self):
        return f"{self.date} - {self.name} (${self.amount})"


class SavingsGoal(BaseModel):
    """Savings Goals (Emergency Fund, House, etc.)"""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="savings_goals")
    name = models.CharField(max_length=255, default="Emergency Fund")
    target_amount = models.DecimalField(max_digits=65, decimal_places=30)
    target_date = models.DateField(null=True, blank=True)
    current_amount = models.DecimalField(max_digits=65, decimal_places=30, default=0)
    accounts = models.ManyToManyField('Account', blank=True, related_name="savings_goals")

    class Meta:
        ordering = ("-created_at",)

    def progress_percent(self):
        if self.target_amount and self.target_amount > 0:
            return min((self.current_amount / self.target_amount) * 100, 100)
        return 0

    def __str__(self):
        return f"{self.name} - ${self.target_amount}"