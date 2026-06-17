from django import forms
from .models import CashFlowEntry, FinancialGoal

class CashFlowEntryForm(forms.ModelForm):
    class Meta:
        model = CashFlowEntry
        fields = ['transaction_date', 'description', 'amount', 'entry_type', 'category']
        widgets = {
            'transaction_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'description': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Digite a descrição'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0,00'}),
            'entry_type': forms.Select(attrs={'class': 'form-control'}, choices=[
                ('IN', '📈 Entrada (Receita)'),
                ('OUT', '📉 Saída (Despesa)'),
            ]),
            'category': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex: Vendas, Aluguel, Salários'}),
        }
        labels = {
            'transaction_date': 'Data da Transação',
            'description': 'Descrição',
            'amount': 'Valor (MZN)',
            'entry_type': 'Tipo',
            'category': 'Categoria',
        }
class FinancialGoalForm(forms.ModelForm):
    class Meta:
        model = FinancialGoal
        fields = ['month', 'goal_entradas', 'goal_saidas', 'goal_saldo']
        widgets = {
            'month': forms.DateInput(attrs={'type': 'month', 'class': 'form-control'}),
            'goal_entradas': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0,00'}),
            'goal_saidas': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0,00'}),
            'goal_saldo': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0,00'}),
        }
        labels = {
            'month': 'Mês de Referência',
            'goal_entradas': 'Meta de Entradas (MZN)',
            'goal_saidas': 'Meta de Saídas (MZN)',
            'goal_saldo': 'Meta de Saldo (MZN)',
        }