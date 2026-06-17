from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal

class Company(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nome da Empresa")
    tax_id = models.CharField(max_length=30, unique=True, verbose_name="NUIT")
    phone = models.CharField(max_length=15, blank=True, verbose_name="Telefone")
    email = models.EmailField(blank=True, verbose_name="Email")
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"
        indexes = [models.Index(fields=["tax_id"])]
    
    def __str__(self):
        return self.name

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="users")
    role = models.CharField(max_length=20, default="admin", choices=[
        ("admin", "Administrador"),
        ("viewer", "Visualizador"),
    ])
    
    def __str__(self):
        return f"{self.user.username} - {self.company.name}"

class SubscriptionPlan(models.Model):
    PLAN_TYPES = [
        ("M", "Mensal"),
        ("T", "Trimestral"),
        ("A", "Anual"),
    ]
    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name="subscription")
    plan_type = models.CharField(max_length=1, choices=PLAN_TYPES)
    price_mzn = models.DecimalField(max_digits=10, decimal_places=2)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    
    def __str__(self):
        return f"{self.company.name} - {self.get_plan_type_display()}"

class CashFlowEntry(models.Model):
    ENTRY_TYPES = [
        ("IN", "Entrada"),
        ("OUT", "Saída"),
    ]
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="entries")
    description = models.CharField(max_length=255, verbose_name="Descrição")
    amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Valor")
    entry_type = models.CharField(max_length=3, choices=ENTRY_TYPES, verbose_name="Tipo")
    transaction_date = models.DateField(verbose_name="Data da Transação")
    category = models.CharField(max_length=100, blank=True, verbose_name="Categoria")
    created_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=20, default="manual", choices=[
        ("manual", "Entrada Manual"),
        ("upload", "Upload de Planilha"),
    ])
    
    class Meta:
        verbose_name = "Lançamento"
        verbose_name_plural = "Lançamentos"
        indexes = [
            models.Index(fields=["company", "transaction_date"]),
            models.Index(fields=["entry_type"]),
        ]
        ordering = ['-transaction_date']
    
    def __str__(self):
        return f"{self.description} - {self.amount} MZN"

class UploadHistory(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="uploads")
    filename = models.CharField(max_length=255, verbose_name="Nome do Arquivo")
    rows_processed = models.IntegerField(default=0, verbose_name="Linhas Processadas")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default="processing", choices=[
        ("processing", "Processando"),
        ("success", "Sucesso"),
        ("error", "Erro"),
    ])
    error_message = models.TextField(blank=True, verbose_name="Mensagem de Erro")
    
    class Meta:
        verbose_name = "Histórico de Upload"
        verbose_name_plural = "Históricos de Upload"
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f"{self.filename} - {self.get_status_display()}"
class FinancialGoal(models.Model):
    """Metas financeiras mensais"""
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="goals")
    month = models.DateField(verbose_name="Mês de Referência")  # Primeiro dia do mês
    goal_entradas = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Meta Entradas")
    goal_saidas = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Meta Saídas")
    goal_saldo = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name="Meta Saldo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Meta Financeira"
        verbose_name_plural = "Metas Financeiras"
        unique_together = ['company', 'month']
        ordering = ['-month']

    def __str__(self):
        return f"{self.company.name} - {self.month.strftime('%b/%Y')}"