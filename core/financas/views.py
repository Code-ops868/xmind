from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Sum, Q
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
from django.http import FileResponse
import pandas as pd
import os
import io
import re
from difflib import get_close_matches

from .models import Company, CashFlowEntry, UploadHistory, FinancialGoal
from .forms import CashFlowEntryForm, FinancialGoalForm


# ==================== RELATÓRIOS PDF ====================
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

# ==================== GRÁFICOS ====================
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


# ==================== VIEWS PRINCIPAIS ====================

@login_required
def dashboard(request):
    """View principal do dashboard"""
    company = request.user.profile.company
    return render(request, 'financas/dashboard.html', {'company': company})


@login_required
def upload_page(request):
    """Página de upload"""
    return render(request, 'financas/upload.html')


# ==================== VIEWS DE API (HTMX) ====================

@login_required
def get_indicators(request):
    """Retorna os indicadores financeiros via HTMX"""
    company = request.user.profile.company
    today = timezone.now().date()
    first_day_month = today.replace(day=1)
    
    total_entradas = CashFlowEntry.objects.filter(
        company=company, entry_type='IN', transaction_date__gte=first_day_month
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    
    total_saidas = CashFlowEntry.objects.filter(
        company=company, entry_type='OUT', transaction_date__gte=first_day_month
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    
    saldo_atual = CashFlowEntry.objects.filter(company=company).aggregate(
        total=Sum('amount', filter=Q(entry_type='IN')) - Sum('amount', filter=Q(entry_type='OUT'))
    )['total'] or Decimal('0')
    
    ponto_equilibrio = total_saidas / Decimal('0.7') if total_saidas > 0 else Decimal('0')
    
    return render(request, 'financas/partials/indicators.html', {
        'total_entradas': total_entradas,
        'total_saidas': total_saidas,
        'saldo_atual': saldo_atual,
        'ponto_equilibrio': ponto_equilibrio,
    })


@login_required
def get_recent_transactions(request):
    """Retorna as últimas transações com filtros via HTMX"""
    company = request.user.profile.company
    queryset = CashFlowEntry.objects.filter(company=company)
    
    periodo = request.GET.get('periodo', '30')
    dias = {'7': 7, '30': 30, '90': 90}.get(periodo, 30)
    queryset = queryset.filter(transaction_date__gte=timezone.now().date() - timedelta(days=dias))
    
    tipo = request.GET.get('tipo', '')
    if tipo in ['IN', 'OUT']:
        queryset = queryset.filter(entry_type=tipo)
    
    categoria = request.GET.get('categoria', '')
    if categoria:
        queryset = queryset.filter(category__icontains=categoria)
    
    busca = request.GET.get('busca', '')
    if busca:
        queryset = queryset.filter(Q(description__icontains=busca) | Q(category__icontains=busca))
    
    transactions = queryset[:20]
    
    categorias = [c for c in CashFlowEntry.objects.filter(company=company).values_list('category', flat=True).distinct() if c]
    
    return render(request, 'financas/partials/transactions.html', {
        'transactions': transactions,
        'periodo': periodo,
        'tipo': tipo,
        'categoria': categoria,
        'busca': busca,
        'categorias': sorted(set(categorias)),
    })


@login_required
def get_upload_history(request):
    """Retorna o histórico de uploads via HTMX"""
    company = request.user.profile.company
    history = UploadHistory.objects.filter(company=company)[:10]
    return render(request, 'financas/partials/upload_history.html', {'history': history})


@login_required
def get_chart_data(request):
    """Retorna dados para o gráfico de tendência"""
    company = request.user.profile.company
    today = timezone.now().date()
    months, entradas_mes, saidas_mes = [], [], []
    
    for i in range(5, -1, -1):
        month_date = today.replace(day=1) - timedelta(days=30*i)
        first_day = month_date.replace(day=1)
        if month_date.month == 12:
            last_day = month_date.replace(day=31)
        else:
            last_day = month_date.replace(month=month_date.month+1, day=1) - timedelta(days=1)
        
        months.append(first_day.strftime('%b/%Y'))
        entradas_mes.append(float(CashFlowEntry.objects.filter(
            company=company, entry_type='IN', transaction_date__gte=first_day, transaction_date__lte=last_day
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')))
        saidas_mes.append(float(CashFlowEntry.objects.filter(
            company=company, entry_type='OUT', transaction_date__gte=first_day, transaction_date__lte=last_day
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')))
    
    return JsonResponse({'months': months, 'entradas': entradas_mes, 'saidas': saidas_mes})


@login_required
def get_category_chart_data(request):
    """Retorna dados para o gráfico de pizza por categoria"""
    try:
        company = request.user.profile.company
        periodo = request.GET.get('periodo', '30')
        dias = {'7': 7, '30': 30, '90': 90}.get(periodo, 30)
        data_inicio = timezone.now().date() - timedelta(days=dias)
        
        entradas_categoria = CashFlowEntry.objects.filter(
            company=company, entry_type='IN', transaction_date__gte=data_inicio
        ).values('category').annotate(total=Sum('amount')).order_by('-total')
        
        saidas_categoria = CashFlowEntry.objects.filter(
            company=company, entry_type='OUT', transaction_date__gte=data_inicio
        ).values('category').annotate(total=Sum('amount')).order_by('-total')
        
        cores_entradas = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40']
        cores_saidas = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#FFA07A', '#98D8C8', '#F7DC6F']
        
        def formatar_dados(dados, cores):
            labels, values, colors = [], [], []
            for idx, item in enumerate(dados):
                labels.append(item['category'] if item['category'] else 'Sem Categoria')
                values.append(float(item['total']))
                colors.append(cores[idx % len(cores)])
            return {'labels': labels, 'values': values, 'colors': colors}
        
        return JsonResponse({
            'entradas': formatar_dados(entradas_categoria, cores_entradas),
            'saidas': formatar_dados(saidas_categoria, cores_saidas)
        })
    except Exception as e:
        return JsonResponse({'error': str(e), 'entradas': {'labels': [], 'values': [], 'colors': []}, 'saidas': {'labels': [], 'values': [], 'colors': []}})


@login_required
def get_monthly_comparison(request):
    """Retorna dados para comparação mês a mês"""
    try:
        company = request.user.profile.company
        today = timezone.now().date()
        months, entradas_mes, saidas_mes, saldo_mes = [], [], [], []
        
        for i in range(5, -1, -1):
            month_date = today.replace(day=1) - timedelta(days=30*i)
            first_day = month_date.replace(day=1)
            if month_date.month == 12:
                last_day = month_date.replace(day=31)
            else:
                last_day = month_date.replace(month=month_date.month+1, day=1) - timedelta(days=1)
            
            months.append(first_day.strftime('%b/%Y'))
            entradas = CashFlowEntry.objects.filter(company=company, entry_type='IN', transaction_date__gte=first_day, transaction_date__lte=last_day).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            saidas = CashFlowEntry.objects.filter(company=company, entry_type='OUT', transaction_date__gte=first_day, transaction_date__lte=last_day).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            entradas_mes.append(float(entradas))
            saidas_mes.append(float(saidas))
            saldo_mes.append(float(entradas - saidas))
        
        return JsonResponse({'months': months, 'entradas': entradas_mes, 'saidas': saidas_mes, 'saldo': saldo_mes})
    except Exception as e:
        return JsonResponse({'error': str(e), 'months': [], 'entradas': [], 'saidas': [], 'saldo': []})


# ==================== ENTRADA MANUAL ====================

@login_required
def add_transaction(request):
    """Adicionar novo lançamento manual"""
    if request.method == 'POST':
        form = CashFlowEntryForm(request.POST)
        if form.is_valid():
            transaction = form.save(commit=False)
            transaction.company = request.user.profile.company
            transaction.source = 'manual'
            transaction.save()
            return render(request, 'financas/partials/add_success.html', {'success': True, 'message': 'Lançamento adicionado com sucesso!'})
        return render(request, 'financas/partials/add_form.html', {'form': form, 'error': 'Por favor, corrija os erros abaixo.'})
    
    return render(request, 'financas/partials/add_form.html', {'form': CashFlowEntryForm()})


@login_required
def get_add_form(request):
    """Retorna o formulário de adição via HTMX"""
    return render(request, 'financas/partials/add_form.html', {'form': CashFlowEntryForm()})


# ==================== EDIÇÃO E EXCLUSÃO ====================

@login_required
def edit_transaction(request, transaction_id):
    """Editar uma transação existente"""
    transaction = get_object_or_404(CashFlowEntry, id=transaction_id, company=request.user.profile.company)
    
    if request.method == 'POST':
        form = CashFlowEntryForm(request.POST, instance=transaction)
        if form.is_valid():
            form.save()
            return render(request, 'financas/partials/edit_success.html', {'success': True, 'message': 'Transação atualizada com sucesso!'})
        return render(request, 'financas/partials/edit_form.html', {'form': form, 'transaction': transaction, 'error': 'Por favor, corrija os erros abaixo.'})
    
    return render(request, 'financas/partials/edit_form.html', {'form': CashFlowEntryForm(instance=transaction), 'transaction': transaction})


@login_required
def delete_transaction(request, transaction_id):
    """Excluir uma transação"""
    transaction = get_object_or_404(CashFlowEntry, id=transaction_id, company=request.user.profile.company)
    
    if request.method == 'POST':
        transaction.delete()
        return render(request, 'financas/partials/delete_success.html', {'success': True, 'message': 'Transação excluída com sucesso!'})
    
    return render(request, 'financas/partials/delete_confirm.html', {'transaction': transaction})


@login_required
def get_edit_form(request, transaction_id):
    """Retorna o formulário de edição via HTMX"""
    transaction = get_object_or_404(CashFlowEntry, id=transaction_id, company=request.user.profile.company)
    return render(request, 'financas/partials/edit_form.html', {'form': CashFlowEntryForm(instance=transaction), 'transaction': transaction})


# ==================== LIMPAR HISTÓRICO ====================

@login_required
def clear_upload_history(request):
    """Limpa todo o histórico de uploads da empresa"""
    company = request.user.profile.company
    
    if request.method == 'POST':
        count = UploadHistory.objects.filter(company=company).count()
        UploadHistory.objects.filter(company=company).delete()
        return render(request, 'financas/partials/upload_history.html', {
            'history': [],
            'success': f'{count} registros removidos do histórico com sucesso!',
            'cleared': True
        })
    
    return render(request, 'financas/partials/upload_history.html', {
        'history': UploadHistory.objects.filter(company=company)[:10],
        'cleared': False
    })


# ==================== RELATÓRIOS PDF ====================

def criar_estilos_pdf():
    """Cria e retorna os estilos padrão para relatórios PDF"""
    styles = getSampleStyleSheet()
    
    estilos = {
        'titulo': ParagraphStyle('TituloStyle', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=18, textColor=colors.black, alignment=TA_CENTER, spaceAfter=12),
        'subtitulo': ParagraphStyle('SubtituloStyle', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=12, textColor=colors.black, alignment=TA_CENTER, spaceAfter=12),
        'subtitulo_menor': ParagraphStyle('SubtituloMenor', parent=styles['Heading3'], fontName='Helvetica-Bold', fontSize=11, textColor=colors.black, alignment=TA_CENTER, spaceAfter=10),
        'normal': ParagraphStyle('NormalStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=10, textColor=colors.black, alignment=TA_LEFT, spaceAfter=4),
        'info': ParagraphStyle('InfoStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=9, textColor=colors.black, alignment=TA_LEFT, spaceAfter=2),
        'cabecalho': ParagraphStyle('CabecalhoStyle', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, textColor=colors.white, alignment=TA_CENTER),
        'dados': ParagraphStyle('DadosStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=8, textColor=colors.black, alignment=TA_LEFT),
        'dados_right': ParagraphStyle('DadosRightStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=8, textColor=colors.black, alignment=TA_RIGHT),
        'rodape': ParagraphStyle('RodapeStyle', parent=styles['Normal'], fontName='Helvetica', fontSize=8, textColor=colors.grey, alignment=TA_CENTER, spaceAfter=4),
    }
    return estilos


def adicionar_cabecalho_relatorio(elements, estilos, titulo, company, periodo, tipo, categoria, busca):
    """Adiciona cabeçalho padrão aos relatórios"""
    elements.append(Paragraph(titulo, estilos['titulo']))
    elements.append(Paragraph("Fluxo de Caixa", estilos['subtitulo']))
    elements.append(Spacer(1, 0.3*cm))
    
    elements.append(Paragraph(f"<b>Empresa:</b> {company.name}", estilos['info']))
    elements.append(Paragraph(f"<b>NUIT:</b> {company.tax_id}", estilos['info']))
    elements.append(Paragraph(f"<b>Data do Relatório:</b> {timezone.now().strftime('%d/%m/%Y %H:%M')}", estilos['info']))
    elements.append(Paragraph(f"<b>Período:</b> Últimos {periodo} dias", estilos['info']))
    
    if tipo == 'IN':
        elements.append(Paragraph("<b>Tipo:</b> Apenas Entradas", estilos['info']))
    elif tipo == 'OUT':
        elements.append(Paragraph("<b>Tipo:</b> Apenas Saídas", estilos['info']))
    if categoria:
        elements.append(Paragraph(f"<b>Categoria:</b> {categoria}", estilos['info']))
    if busca:
        elements.append(Paragraph(f"<b>Busca:</b> {busca}", estilos['info']))
    
    elements.append(Spacer(1, 0.3*cm))


def adicionar_tabela_totais(elements, estilos, total_entradas, total_saidas, saldo):
    """Adiciona tabela de totais ao relatório"""
    dados = [
        ['Total de Entradas', f"{total_entradas:,.2f} MZN"],
        ['Total de Saídas', f"{total_saidas:,.2f} MZN"],
        ['Saldo Final', f"{saldo:,.2f} MZN"]
    ]
    
    tabela = Table(dados, colWidths=[5*cm, 4*cm])
    tabela.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('BACKGROUND', (0, 0), (-1, -1), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(tabela)
    elements.append(Spacer(1, 0.4*cm))


def adicionar_rodape(elements, estilos):
    """Adiciona rodapé padronizado aos relatórios"""
    elements.append(Spacer(1, 0.3*cm))
    elements.append(Paragraph("<hr color='#ffd700' width='100%' size='2'>", estilos['normal']))
    elements.append(Paragraph(
        f"<i>Documento processado pelo sistema 'XMIND' - {timezone.now().strftime('%d/%m/%Y às %H:%M')}</i>",
        estilos['rodape']
    ))
    elements.append(Paragraph("© 2026 XMIND - Todos os direitos reservados", estilos['rodape']))


def adicionar_tabela_transacoes(elements, estilos, queryset):
    """Adiciona tabela de transações ao relatório"""
    elements.append(Paragraph("LISTA DE TRANSAÇÕES", estilos['subtitulo']))
    elements.append(Spacer(1, 0.2*cm))
    
    cabecalhos = [Paragraph(h, estilos['cabecalho']) for h in ['Data', 'Descrição', 'Categoria', 'Valor', 'Tipo']]
    dados_tabela = [cabecalhos]
    
    for entry in queryset[:100]:
        dados_tabela.append([
            Paragraph(entry.transaction_date.strftime('%d/%m/%Y'), estilos['dados']),
            Paragraph(entry.description[:40], estilos['dados']),
            Paragraph(entry.category or '-', estilos['dados']),
            Paragraph(f"{entry.amount:,.2f}", estilos['dados_right']),
            Paragraph('Entrada' if entry.entry_type == 'IN' else 'Saída', estilos['dados'])
        ])
    
    if len(dados_tabela) == 1:
        dados_tabela.append([Paragraph('', estilos['dados']), Paragraph('Nenhuma transação encontrada', estilos['dados']), Paragraph('', estilos['dados']), Paragraph('', estilos['dados']), Paragraph('', estilos['dados'])])
    
    tabela = Table(dados_tabela, colWidths=[2*cm, 4.5*cm, 3.5*cm, 3*cm, 2.5*cm])
    tabela.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#141a20')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('PADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('PADDING', (0, 1), (-1, -1), 6),
        ('ALIGN', (3, 1), (3, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
    ]))
    elements.append(tabela)
    elements.append(Spacer(1, 0.5*cm))


def obter_filtros_e_queryset(request, company):
    """Obtém os filtros da requisição e aplica ao queryset"""
    periodo = request.GET.get('periodo', '30')
    tipo = request.GET.get('tipo', '')
    categoria = request.GET.get('categoria', '')
    busca = request.GET.get('busca', '')
    
    dias = {'7': 7, '30': 30, '90': 90}.get(periodo, 30)
    queryset = CashFlowEntry.objects.filter(company=company, transaction_date__gte=timezone.now().date() - timedelta(days=dias))
    
    if tipo in ['IN', 'OUT']:
        queryset = queryset.filter(entry_type=tipo)
    if categoria:
        queryset = queryset.filter(category__icontains=categoria)
    if busca:
        queryset = queryset.filter(Q(description__icontains=busca) | Q(category__icontains=busca))
    
    return periodo, tipo, categoria, busca, queryset


@login_required
def generate_report_pdf(request):
    """Gera relatório PDF das transações com filtros"""
    company = request.user.profile.company
    periodo, tipo, categoria, busca, queryset = obter_filtros_e_queryset(request, company)
    queryset = queryset.order_by('-transaction_date')
    
    total_entradas = queryset.filter(entry_type='IN').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    total_saidas = queryset.filter(entry_type='OUT').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    saldo = total_entradas - total_saidas
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm, title="Relatório Financeiro")
    estilos = criar_estilos_pdf()
    elements = []
    
    adicionar_cabecalho_relatorio(elements, estilos, "RELATÓRIO FINANCEIRO", company, periodo, tipo, categoria, busca)
    adicionar_tabela_totais(elements, estilos, total_entradas, total_saidas, saldo)
    adicionar_tabela_transacoes(elements, estilos, queryset)
    adicionar_rodape(elements, estilos)
    
    doc.build(elements)
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=True, filename=f'relatorio_{timezone.now().strftime("%Y%m%d_%H%M")}.pdf')


def criar_grafico_pizza(dados, titulo):
    """Cria um gráfico de pizza donut com matplotlib e retorna como BytesIO"""
    if not dados or len(dados) == 0:
        return None
    
    labels = []
    values = []
    cores = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40']
    
    for idx, item in enumerate(dados):
        labels.append(item['category'] if item['category'] else 'Sem Categoria')
        values.append(float(item['total']))
    
    fig, ax = plt.subplots(figsize=(6, 4), facecolor='white')
    wedges, texts, autotexts = ax.pie(
        values, labels=labels, autopct=lambda pct: f'{pct:.1f}%' if pct > 2 else '',
        startangle=90, colors=cores[:len(labels)],
        wedgeprops={'width': 0.6, 'edgecolor': 'white', 'linewidth': 2},
        textprops={'fontsize': 8, 'color': 'black'}, pctdistance=0.75
    )
    
    for text in texts:
        text.set_fontsize(7)
        text.set_color('black')
    for autotext in autotexts:
        autotext.set_fontsize(7)
        autotext.set_color('black')
        autotext.set_weight('bold')
    
    ax.set_title(titulo, fontsize=10, fontweight='bold', color='black', pad=8)
    ax.axis('equal')
    
    img_buffer = io.BytesIO()
    plt.tight_layout()
    fig.savefig(img_buffer, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    img_buffer.seek(0)
    return img_buffer


@login_required
def generate_report_with_charts(request):
    """Gera relatório PDF com gráficos por categoria - 2 páginas"""
    company = request.user.profile.company
    periodo, tipo, categoria, busca, queryset = obter_filtros_e_queryset(request, company)
    
    data_inicio = timezone.now().date() - timedelta(days={'7': 7, '30': 30, '90': 90}.get(periodo, 30))
    entradas_categoria = CashFlowEntry.objects.filter(
        company=company, entry_type='IN', transaction_date__gte=data_inicio
    ).values('category').annotate(total=Sum('amount')).order_by('-total')
    
    saidas_categoria = CashFlowEntry.objects.filter(
        company=company, entry_type='OUT', transaction_date__gte=data_inicio
    ).values('category').annotate(total=Sum('amount')).order_by('-total')
    
    total_entradas = queryset.filter(entry_type='IN').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    total_saidas = queryset.filter(entry_type='OUT').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    saldo = total_entradas - total_saidas
    
    grafico_entradas = criar_grafico_pizza(entradas_categoria, 'Entradas por Categoria')
    grafico_saidas = criar_grafico_pizza(saidas_categoria, 'Saídas por Categoria')
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), 
                           rightMargin=1.5*cm, leftMargin=1.5*cm,
                           topMargin=1.5*cm, bottomMargin=1.5*cm)
    estilos = criar_estilos_pdf()
    elements = []
    
    # PÁGINA 1 - INFORMAÇÕES GERAIS E TOTAIS
    elements.append(Paragraph("RELATÓRIO POR CATEGORIA", estilos['titulo']))
    elements.append(Paragraph("Fluxo de Caixa", estilos['subtitulo']))
    elements.append(Spacer(1, 0.5*cm))
    
    elements.append(Paragraph("<hr color='#ffd700' width='80%' size='2'>", estilos['normal']))
    elements.append(Spacer(1, 0.3*cm))
    
    elements.append(Paragraph(f"<b>Empresa:</b> {company.name}", estilos['info']))
    elements.append(Paragraph(f"<b>NUIT:</b> {company.tax_id}", estilos['info']))
    elements.append(Paragraph(f"<b>Data do Relatório:</b> {timezone.now().strftime('%d/%m/%Y %H:%M')}", estilos['info']))
    elements.append(Paragraph(f"<b>Período:</b> Últimos {periodo} dias", estilos['info']))
    
    if tipo == 'IN':
        elements.append(Paragraph("<b>Tipo:</b> Apenas Entradas", estilos['info']))
    elif tipo == 'OUT':
        elements.append(Paragraph("<b>Tipo:</b> Apenas Saídas", estilos['info']))
    if categoria:
        elements.append(Paragraph(f"<b>Categoria:</b> {categoria}", estilos['info']))
    if busca:
        elements.append(Paragraph(f"<b>Busca:</b> {busca}", estilos['info']))
    
    elements.append(Spacer(1, 0.5*cm))
    
    elements.append(Paragraph("<hr color='#ffd700' width='80%' size='2'>", estilos['normal']))
    elements.append(Spacer(1, 0.3*cm))
    
    dados_totais = [
        ['Total de Entradas', f"{total_entradas:,.2f} MZN"],
        ['Total de Saídas', f"{total_saidas:,.2f} MZN"],
        ['Saldo Final', f"{saldo:,.2f} MZN"]
    ]
    
    tabela_totais = Table(dados_totais, colWidths=[6*cm, 5*cm])
    tabela_totais.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (1, 0), (1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (-1, -1), colors.white),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('PADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(tabela_totais)
    
    elements.append(PageBreak())
    
    # PÁGINA 2 - GRÁFICOS E TABELA POR CATEGORIA
    dados_graficos = [[], []]
    
    if grafico_entradas:
        img_entradas = Image(grafico_entradas, width=8*cm, height=6*cm)
        dados_graficos[0].append(img_entradas)
    else:
        dados_graficos[0].append(Paragraph("Sem dados de entradas", estilos['normal']))
    
    if grafico_saidas:
        img_saidas = Image(grafico_saidas, width=8*cm, height=6*cm)
        dados_graficos[0].append(img_saidas)
    else:
        dados_graficos[0].append(Paragraph("Sem dados de saídas", estilos['normal']))
    
    dados_graficos[1] = [
        Paragraph("<b>Entradas por Categoria</b>", estilos['subtitulo_menor']),
        Paragraph("<b>Saídas por Categoria</b>", estilos['subtitulo_menor'])
    ]
    
    tabela_graficos = Table(dados_graficos, colWidths=[8.5*cm, 8.5*cm])
    tabela_graficos.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (-1, -1), colors.white),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(tabela_graficos)
    elements.append(Spacer(1, 0.3*cm))
    
    elements.append(Paragraph("<hr color='#ffd700' width='80%' size='2'>", estilos['normal']))
    elements.append(Spacer(1, 0.3*cm))
    
    elements.append(Paragraph("TOTAIS POR CATEGORIA", estilos['subtitulo']))
    elements.append(Spacer(1, 0.2*cm))
    
    entradas_dict = {item['category'] or 'Sem Categoria': float(item['total']) for item in entradas_categoria}
    saidas_dict = {item['category'] or 'Sem Categoria': float(item['total']) for item in saidas_categoria}
    
    cat_data = [['Categoria', 'Entradas (MZN)', 'Saídas (MZN)', 'Saldo (MZN)']]
    for cat in sorted(set(list(entradas_dict.keys()) + list(saidas_dict.keys()))):
        ent_val = entradas_dict.get(cat, 0)
        sai_val = saidas_dict.get(cat, 0)
        cat_data.append([cat, f"{ent_val:,.2f}", f"{sai_val:,.2f}", f"{ent_val - sai_val:,.2f}"])
    
    if len(cat_data) == 1:
        cat_data.append(['Nenhuma categoria encontrada', '-', '-', '-'])
    
    tabela_cat = Table(cat_data, colWidths=[5.5*cm, 4*cm, 4*cm, 4*cm])
    tabela_cat.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#141a20')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 1), (3, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(tabela_cat)
    elements.append(Spacer(1, 0.3*cm))
    
    adicionar_rodape(elements, estilos)
    
    doc.build(elements)
    buffer.seek(0)
    response = FileResponse(buffer, as_attachment=True, filename=f'relatorio_categoria_{timezone.now().strftime("%Y%m%d_%H%M")}.pdf')
    response['Content-Type'] = 'application/pdf'
    return response


# ==================== RELATÓRIO DE PREVISÃO ====================

@login_required
def generate_forecast_report(request):
    """Gera relatório PDF de previsão de fluxo de caixa"""
    company = request.user.profile.company
    
    today = timezone.now().date()
    meses_historico = 6
    meses_previsao = 6
    
    dados_historicos = []
    for i in range(meses_historico - 1, -1, -1):
        month_date = today.replace(day=1) - timedelta(days=30*i)
        first_day = month_date.replace(day=1)
        if month_date.month == 12:
            last_day = month_date.replace(day=31)
        else:
            last_day = month_date.replace(month=month_date.month+1, day=1) - timedelta(days=1)
        
        entradas = CashFlowEntry.objects.filter(
            company=company, entry_type='IN',
            transaction_date__gte=first_day, transaction_date__lte=last_day
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        saidas = CashFlowEntry.objects.filter(
            company=company, entry_type='OUT',
            transaction_date__gte=first_day, transaction_date__lte=last_day
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        dados_historicos.append({
            'mes': first_day.strftime('%b/%Y'),
            'entradas': float(entradas),
            'saidas': float(saidas),
            'saldo': float(entradas - saidas)
        })
    
    if dados_historicos:
        media_entradas = sum(d['entradas'] for d in dados_historicos) / len(dados_historicos)
        media_saidas = sum(d['saidas'] for d in dados_historicos) / len(dados_historicos)
        
        if len(dados_historicos) >= 3:
            tendencia_entradas = (dados_historicos[-1]['entradas'] - dados_historicos[0]['entradas']) / len(dados_historicos)
            tendencia_saidas = (dados_historicos[-1]['saidas'] - dados_historicos[0]['saidas']) / len(dados_historicos)
        else:
            tendencia_entradas = 0
            tendencia_saidas = 0
    else:
        media_entradas = 0
        media_saidas = 0
        tendencia_entradas = 0
        tendencia_saidas = 0
    
    previsao = []
    saldo_atual = dados_historicos[-1]['saldo'] if dados_historicos else 0
    
    for i in range(1, meses_previsao + 1):
        month_date = today.replace(day=1) + timedelta(days=30*i)
        mes_nome = month_date.strftime('%b/%Y')
        
        fator_entradas = 1 + (tendencia_entradas / media_entradas) * i if media_entradas > 0 else 1
        fator_saidas = 1 + (tendencia_saidas / media_saidas) * i if media_saidas > 0 else 1
        
        entradas_projetadas = media_entradas * fator_entradas
        saidas_projetadas = media_saidas * fator_saidas
        
        entradas_projetadas = max(entradas_projetadas, 0)
        saidas_projetadas = max(saidas_projetadas, 0)
        
        saldo_projetado = saldo_atual + entradas_projetadas - saidas_projetadas
        
        previsao.append({
            'mes': mes_nome,
            'entradas': entradas_projetadas,
            'saidas': saidas_projetadas,
            'saldo': saldo_projetado
        })
        
        saldo_atual = saldo_projetado
    
    grafico_previsao = criar_grafico_previsao(dados_historicos, previsao)
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                           rightMargin=1.5*cm, leftMargin=1.5*cm,
                           topMargin=1.5*cm, bottomMargin=1.5*cm)
    estilos = criar_estilos_pdf()
    elements = []
    
    elements.append(Paragraph("RELATÓRIO DE PREVISÃO DE FLUXO DE CAIXA", estilos['titulo']))
    elements.append(Paragraph("Projeção Financeira", estilos['subtitulo']))
    elements.append(Spacer(1, 0.3*cm))
    
    elements.append(Paragraph(f"<b>Empresa:</b> {company.name}", estilos['info']))
    elements.append(Paragraph(f"<b>NUIT:</b> {company.tax_id}", estilos['info']))
    elements.append(Paragraph(f"<b>Data do Relatório:</b> {timezone.now().strftime('%d/%m/%Y %H:%M')}", estilos['info']))
    elements.append(Paragraph(f"<b>Período Base:</b> Últimos {meses_historico} meses", estilos['info']))
    elements.append(Paragraph(f"<b>Previsão:</b> Próximos {meses_previsao} meses", estilos['info']))
    elements.append(Spacer(1, 0.3*cm))
    
    elements.append(Paragraph("RESUMO DA PREVISÃO", estilos['subtitulo']))
    elements.append(Spacer(1, 0.1*cm))
    
    ultimo_saldo_real = dados_historicos[-1]['saldo'] if dados_historicos else 0
    saldo_final_projetado = previsao[-1]['saldo'] if previsao else 0
    
    dados_resumo = [
        ['Saldo Atual', f"{ultimo_saldo_real:,.2f} MZN"],
        ['Saldo Projetado (6 meses)', f"{saldo_final_projetado:,.2f} MZN"],
        ['Variação', f"{saldo_final_projetado - ultimo_saldo_real:,.2f} MZN"],
        ['Média Entradas (histórico)', f"{media_entradas:,.2f} MZN"],
        ['Média Saídas (histórico)', f"{media_saidas:,.2f} MZN"],
    ]
    
    tabela_resumo = Table(dados_resumo, colWidths=[6*cm, 5*cm])
    tabela_resumo.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('BACKGROUND', (0, 0), (-1, -1), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
    ]))
    elements.append(tabela_resumo)
    elements.append(Spacer(1, 0.3*cm))
    
    if grafico_previsao:
        img_previsao = Image(grafico_previsao, width=15*cm, height=7*cm)
        elements.append(img_previsao)
    else:
        elements.append(Paragraph("Não foi possível gerar o gráfico de previsão", estilos['normal']))
    
    elements.append(Spacer(1, 0.3*cm))
    
    elements.append(Paragraph("PREVISÃO DETALHADA POR MÊS", estilos['subtitulo']))
    elements.append(Spacer(1, 0.1*cm))
    
    tabela_dados = [['Mês', 'Entradas (MZN)', 'Saídas (MZN)', 'Saldo (MZN)', 'Status']]
    
    for item in dados_historicos:
        saldo_atual_item = item['saldo']
        status = '✅ Positivo' if saldo_atual_item >= 0 else '⚠️ Negativo'
        tabela_dados.append([
            item['mes'],
            f"{item['entradas']:,.2f}",
            f"{item['saidas']:,.2f}",
            f"{saldo_atual_item:,.2f}",
            status
        ])
    
    for item in previsao:
        saldo_projetado = item['saldo']
        status = '📈 Positivo' if saldo_projetado >= 0 else '📉 Negativo'
        tabela_dados.append([
            f"🔮 {item['mes']}",
            f"{item['entradas']:,.2f}",
            f"{item['saidas']:,.2f}",
            f"{saldo_projetado:,.2f}",
            status
        ])
    
    tabela_previsao = Table(tabela_dados, colWidths=[3*cm, 3.5*cm, 3.5*cm, 3.5*cm, 3*cm])
    tabela_previsao.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#141a20')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 1), (3, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('LINEABOVE', (0, len(dados_historicos)+1), (-1, len(dados_historicos)+1), 2, colors.HexColor('#ffd700')),
    ]))
    elements.append(tabela_previsao)
    elements.append(Spacer(1, 0.3*cm))
    
    adicionar_rodape(elements, estilos)
    
    doc.build(elements)
    buffer.seek(0)
    response = FileResponse(buffer, as_attachment=True, filename=f'previsao_fluxo_{timezone.now().strftime("%Y%m%d_%H%M")}.pdf')
    response['Content-Type'] = 'application/pdf'
    return response


def criar_grafico_previsao(dados_historicos, previsao):
    """Cria gráfico de previsão com matplotlib"""
    if not dados_historicos and not previsao:
        return None
    
    labels = []
    entradas = []
    saidas = []
    saldos = []
    
    for item in dados_historicos:
        labels.append(item['mes'])
        entradas.append(item['entradas'])
        saidas.append(item['saidas'])
        saldos.append(item['saldo'])
    
    for item in previsao:
        labels.append(f"🔮{item['mes']}")
        entradas.append(item['entradas'])
        saidas.append(item['saidas'])
        saldos.append(item['saldo'])
    
    fig, ax = plt.subplots(figsize=(12, 6), facecolor='white')
    
    if dados_historicos:
        ax.axvline(x=len(dados_historicos) - 0.5, color='#ffd700', linestyle='--', linewidth=2, alpha=0.7, label='Previsão')
    
    x = range(len(labels))
    ax.plot(x, entradas, 'o-', color='#28a745', linewidth=2, markersize=6, label='Entradas')
    ax.plot(x, saidas, 's-', color='#dc3545', linewidth=2, markersize=6, label='Saídas')
    ax.plot(x, saldos, 'D-', color='#ffd700', linewidth=2, markersize=6, label='Saldo')
    
    ax.fill_between(x, 0, saldos, where=[s >= 0 for s in saldos], color='#28a745', alpha=0.1)
    ax.fill_between(x, 0, saldos, where=[s < 0 for s in saldos], color='#dc3545', alpha=0.1)
    
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    ax.set_title('Previsão de Fluxo de Caixa', fontsize=12, fontweight='bold', color='black', pad=10)
    ax.set_ylabel('Valor (MZN)', fontsize=10, color='black')
    ax.set_xlabel('Período', fontsize=10, color='black')
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(True, alpha=0.2, color='grey')
    ax.set_axisbelow(True)
    ax.tick_params(colors='black')
    
    for spine in ax.spines.values():
        spine.set_color('black')
    
    plt.tight_layout()
    
    img_buffer = io.BytesIO()
    fig.savefig(img_buffer, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    img_buffer.seek(0)
    
    return img_buffer


# ==================== METAS E PERFORMANCE ====================
@login_required
def goals_page(request):
    """Página de definição de metas"""
    company = request.user.profile.company
    goals = FinancialGoal.objects.filter(company=company).order_by('-month')
    return render(request, 'financas/goals.html', {'goals': goals})


#=============================================
@login_required
def add_goal(request):
    """Adicionar nova meta"""
    company = request.user.profile.company
    
    if request.method == 'POST':
        try:
            month = request.POST.get('month')
            goal_entradas = request.POST.get('goal_entradas')
            goal_saidas = request.POST.get('goal_saidas')
            goal_saldo = request.POST.get('goal_saldo')
            
            if not month:
                return render(request, 'financas/goals.html', {
                    'goals': FinancialGoal.objects.filter(company=company).order_by('-month'),
                    'error': 'Selecione um mês.'
                })
            
            # Converter mês para data
            month_date = datetime.strptime(month, '%Y-%m').date()
            
            # Verificar se já existe
            existing = FinancialGoal.objects.filter(company=company, month=month_date).first()
            if existing:
                return render(request, 'financas/goals.html', {
                    'goals': FinancialGoal.objects.filter(company=company).order_by('-month'),
                    'error': 'Já existe uma meta para este mês.'
                })
            
            # Criar meta
            FinancialGoal.objects.create(
                company=company,
                month=month_date,
                goal_entradas=Decimal(goal_entradas) if goal_entradas else Decimal('0'),
                goal_saidas=Decimal(goal_saidas) if goal_saidas else Decimal('0'),
                goal_saldo=Decimal(goal_saldo) if goal_saldo else Decimal('0')
            )
            
            return redirect('financas:goals_page')
            
        except Exception as e:
            return render(request, 'financas/goals.html', {
                'goals': FinancialGoal.objects.filter(company=company).order_by('-month'),
                'error': f'Erro ao salvar: {str(e)}'
            })
    
    return redirect('financas:goals_page')
#==============================================

@login_required
def get_goals_form(request):
    """Retorna o formulário de metas via HTMX"""
    form = FinancialGoalForm()
    return render(request, 'financas/partials/add_goal_form.html', {'form': form})


@login_required
def delete_goal(request, goal_id):
    """Excluir uma meta"""
    goal = get_object_or_404(FinancialGoal, id=goal_id, company=request.user.profile.company)
    if request.method == 'POST':
        goal.delete()
        company = request.user.profile.company
        goals = FinancialGoal.objects.filter(company=company).order_by('-month')
        return render(request, 'financas/goals_content.html', {'goals': goals})
    return render(request, 'financas/goals_content.html', {'goals': FinancialGoal.objects.filter(company=request.user.profile.company).order_by('-month')})

#==========================================================================================================
@login_required
def generate_goals_report(request):
    """Gera relatório PDF de Metas e Performance"""
    company = request.user.profile.company
    
    goals = FinancialGoal.objects.filter(company=company).order_by('-month')
    
    if not goals:
        response = HttpResponse("Nenhuma meta definida. Por favor, defina metas primeiro.")
        response.status_code = 400
        return response
    
    performance_data = []
    for goal in goals:
        month = goal.month
        first_day = month.replace(day=1)
        if month.month == 12:
            last_day = month.replace(day=31)
        else:
            last_day = month.replace(month=month.month+1, day=1) - timedelta(days=1)
        
        real_entradas = CashFlowEntry.objects.filter(
            company=company, entry_type='IN',
            transaction_date__gte=first_day, transaction_date__lte=last_day
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        real_saidas = CashFlowEntry.objects.filter(
            company=company, entry_type='OUT',
            transaction_date__gte=first_day, transaction_date__lte=last_day
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        real_saldo = real_entradas - real_saidas
        
        perf_entradas = (float(real_entradas) / float(goal.goal_entradas) * 100) if goal.goal_entradas > 0 else 0
        perf_saidas = (float(real_saidas) / float(goal.goal_saidas) * 100) if goal.goal_saidas > 0 else 0
        perf_saldo = (float(real_saldo) / float(goal.goal_saldo) * 100) if goal.goal_saldo > 0 else 0
        
        performance_data.append({
            'mes': month.strftime('%b/%Y'),
            'goal_entradas': float(goal.goal_entradas),
            'goal_saidas': float(goal.goal_saidas),
            'goal_saldo': float(goal.goal_saldo),
            'real_entradas': float(real_entradas),
            'real_saidas': float(real_saidas),
            'real_saldo': float(real_saldo),
            'perf_entradas': perf_entradas,
            'perf_saidas': perf_saidas,
            'perf_saldo': perf_saldo,
        })
    
    grafico_performance = criar_grafico_performance(performance_data)
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                           rightMargin=1.5*cm, leftMargin=1.5*cm,
                           topMargin=1.5*cm, bottomMargin=1.5*cm)
    estilos = criar_estilos_pdf()
    elements = []
    
    elements.append(Paragraph("RELATÓRIO DE METAS E PERFORMANCE", estilos['titulo']))
    elements.append(Paragraph("Acompanhamento de Metas Financeiras", estilos['subtitulo']))
    elements.append(Spacer(1, 0.3*cm))
    
    elements.append(Paragraph(f"<b>Empresa:</b> {company.name}", estilos['info']))
    elements.append(Paragraph(f"<b>NUIT:</b> {company.tax_id}", estilos['info']))
    elements.append(Paragraph(f"<b>Data do Relatório:</b> {timezone.now().strftime('%d/%m/%Y %H:%M')}", estilos['info']))
    elements.append(Spacer(1, 0.3*cm))
    
    elements.append(Paragraph("RESUMO DE PERFORMANCE", estilos['subtitulo']))
    elements.append(Spacer(1, 0.1*cm))
    
    if performance_data:
        media_perf_entradas = sum(d['perf_entradas'] for d in performance_data) / len(performance_data)
        media_perf_saidas = sum(d['perf_saidas'] for d in performance_data) / len(performance_data)
        media_perf_saldo = sum(d['perf_saldo'] for d in performance_data) / len(performance_data)
    else:
        media_perf_entradas = media_perf_saidas = media_perf_saldo = 0
    
    dados_resumo = [
        ['Indicador', 'Média de Performance'],
        ['Entradas', f"{media_perf_entradas:.1f}%"],
        ['Saídas', f"{media_perf_saidas:.1f}%"],
        ['Saldo', f"{media_perf_saldo:.1f}%"],
    ]
    
    tabela_resumo = Table(dados_resumo, colWidths=[5*cm, 5*cm])
    tabela_resumo.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#141a20')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('PADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(tabela_resumo)
    elements.append(Spacer(1, 0.3*cm))
    
    if grafico_performance:
        img_performance = Image(grafico_performance, width=15*cm, height=7*cm)
        elements.append(img_performance)
    else:
        elements.append(Paragraph("Não foi possível gerar o gráfico de performance", estilos['normal']))
    
    elements.append(Spacer(1, 0.3*cm))
    
    elements.append(Paragraph("DETALHAMENTO POR MÊS", estilos['subtitulo']))
    elements.append(Spacer(1, 0.1*cm))
    
    tabela_dados = [['Mês', 'Meta Ent.', 'Real Ent.', 'Perf.%', 'Meta Sai.', 'Real Sai.', 'Perf.%', 'Meta Saldo', 'Real Saldo', 'Perf.%']]
    
    for item in performance_data:
        tabela_dados.append([
            item['mes'],
            f"{item['goal_entradas']:,.0f}",
            f"{item['real_entradas']:,.0f}",
            f"{item['perf_entradas']:.1f}%",
            f"{item['goal_saidas']:,.0f}",
            f"{item['real_saidas']:,.0f}",
            f"{item['perf_saidas']:.1f}%",
            f"{item['goal_saldo']:,.0f}",
            f"{item['real_saldo']:,.0f}",
            f"{item['perf_saldo']:.1f}%",
        ])
    
    tabela_detalhada = Table(tabela_dados, colWidths=[2.5*cm, 2.5*cm, 2.5*cm, 2*cm, 2.5*cm, 2.5*cm, 2*cm, 2.5*cm, 2.5*cm, 2*cm])
    tabela_detalhada.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#141a20')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 7),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('FONTSIZE', (0, 1), (-1, -1), 7),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(tabela_detalhada)
    elements.append(Spacer(1, 0.3*cm))
    
    adicionar_rodape(elements, estilos)
    
    doc.build(elements)
    buffer.seek(0)
    response = FileResponse(buffer, as_attachment=True, filename=f'metas_performance_{timezone.now().strftime("%Y%m%d_%H%M")}.pdf')
    response['Content-Type'] = 'application/pdf'
    return response


def criar_grafico_performance(performance_data):
    """Cria gráfico de performance com matplotlib"""
    if not performance_data:
        return None
    
    fig, ax = plt.subplots(figsize=(12, 6), facecolor='white')
    
    meses = [d['mes'] for d in performance_data]
    perf_entradas = [d['perf_entradas'] for d in performance_data]
    perf_saidas = [d['perf_saidas'] for d in performance_data]
    perf_saldo = [d['perf_saldo'] for d in performance_data]
    
    x = range(len(meses))
    width = 0.25
    
    ax.bar([i - width for i in x], perf_entradas, width, label='Entradas', color='#28a745')
    ax.bar(x, perf_saidas, width, label='Saídas', color='#dc3545')
    ax.bar([i + width for i in x], perf_saldo, width, label='Saldo', color='#ffd700')
    
    ax.axhline(y=100, color='#141a20', linestyle='--', linewidth=1.5, alpha=0.5, label='Meta (100%)')
    
    ax.set_xlabel('Mês', fontsize=10, color='black')
    ax.set_ylabel('Performance (%)', fontsize=10, color='black')
    ax.set_title('Performance vs Meta (Realizado / Meta x 100%)', fontsize=12, fontweight='bold', color='black')
    ax.set_xticks(x)
    ax.set_xticklabels(meses, rotation=45, ha='right', fontsize=8)
    ax.legend(loc='upper left', fontsize=9)
    ax.set_ylim(0, max(max(perf_entradas), max(perf_saidas), max(perf_saldo)) * 1.2 + 10)
    ax.grid(True, alpha=0.2, color='grey')
    ax.set_axisbelow(True)
    
    for spine in ax.spines.values():
        spine.set_color('black')
    ax.tick_params(colors='black')
    
    plt.tight_layout()
    
    img_buffer = io.BytesIO()
    fig.savefig(img_buffer, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    img_buffer.seek(0)
    
    return img_buffer


# ==================== PROCESSAMENTO DE UPLOAD ====================

@csrf_exempt
@login_required
def process_upload(request):
    """Processa o upload da planilha"""
    if request.method == 'POST' and request.FILES.get('file'):
        company = request.user.profile.company
        uploaded_file = request.FILES['file']
        filename = uploaded_file.name
        
        if filename.split('.')[-1].lower() not in ['csv', 'xlsx']:
            return render(request, 'financas/partials/upload_result.html', {'success': False, 'message': 'Formato não suportado. Use .csv ou .xlsx'})
        
        temp_path = f"media/temp/{filename}"
        os.makedirs('media/temp', exist_ok=True)
        
        with open(temp_path, 'wb+') as f:
            for chunk in uploaded_file.chunks():
                f.write(chunk)
        
        history = UploadHistory.objects.create(company=company, filename=filename, status='processing')
        
        try:
            rows = process_excel_file(temp_path, company)
            history.status = 'success'
            history.rows_processed = rows
            history.save()
            return render(request, 'financas/partials/upload_result.html', {'success': True, 'message': f'Planilha processada com sucesso! {rows} registros importados.'})
        except Exception as e:
            history.status = 'error'
            history.error_message = str(e)
            history.save()
            return render(request, 'financas/partials/upload_result.html', {'success': False, 'message': f'Erro ao processar: {str(e)}'})
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    
    return render(request, 'financas/partials/upload_result.html', {'success': False, 'message': 'Nenhum arquivo enviado'})


# ==================== PROCESSAMENTO DE EXCEL ====================

def normalizar_texto(texto):
    """Remove acentos, espaços extras e converte para minúsculo"""
    texto = str(texto).strip().lower()
    texto = texto.replace('ç', 'c').replace('ã', 'a').replace('á', 'a')
    texto = texto.replace('é', 'e').replace('ê', 'e').replace('í', 'i')
    texto = texto.replace('ó', 'o').replace('ô', 'o').replace('ú', 'u')
    texto = texto.replace('õ', 'o').replace('â', 'a').replace('à', 'a')
    texto = re.sub(r'[^a-z0-9\s]', '', texto)
    return ' '.join(texto.split())


def similaridade(texto1, texto2):
    """Calcula similaridade entre dois textos"""
    texto1 = normalizar_texto(texto1)
    texto2 = normalizar_texto(texto2)
    if not texto1 or not texto2:
        return 0
    if texto1 in texto2 or texto2 in texto1:
        return 1.0
    try:
        return len(get_close_matches(texto1, [texto2], n=1, cutoff=0.6)) / 1.0
    except:
        return 0


def process_excel_file(file_path, company):
    """Processa qualquer planilha financeira de fluxo de caixa - Detecção automática de cabeçalhos"""
    
    if file_path.endswith('.xlsx'):
        df_raw = pd.read_excel(file_path, header=None, dtype=str)
    else:
        df_raw = pd.read_csv(file_path, header=None, dtype=str)
    df_raw = df_raw.fillna('')
    
    colunas_padrao = {
        'data': ['data', 'date', 'dt', 'dia', 'data_lancamento', 'data_transacao'],
        'descricao': ['descricao', 'description', 'desc', 'historico', 'observacao', 'observacoes', 'nome', 'titulo', 'produto', 'servico'],
        'valor': ['valor', 'amount', 'val', 'value', 'total', 'preco', 'preço', 'vlr', 'montante', 'custo'],
        'tipo': ['tipo', 'type', 'entrada_saida', 'classificacao', 'natureza', 'movimento', 'operacao'],
        'categoria': ['categoria', 'category', 'grupo', 'classificacao', 'rubrica', 'conta']
    }
    
    pontuacao_linhas = []
    for idx in range(len(df_raw)):
        linha = df_raw.iloc[idx].astype(str).tolist()
        linha_texto = ' '.join(linha).lower()
        
        pontuacao = 0
        for tipo, palavras in colunas_padrao.items():
            for palavra in palavras:
                if palavra in linha_texto:
                    pontuacao += 1
                    break
        
        celulas_preenchidas = sum(1 for cell in linha if cell.strip())
        celulas_texto = sum(1 for cell in linha if cell.strip() and not cell.replace('.', '').replace(',', '').replace('-', '').isdigit())
        
        if celulas_preenchidas >= 3:
            pontuacao += 1
        if celulas_texto >= 3:
            pontuacao += 1
        if linha and linha[0].strip() and not linha[0].strip().replace('/', '').replace('-', '').replace('.', '').isdigit():
            pontuacao += 0.5
        
        pontuacao_linhas.append({'indice': idx, 'pontuacao': pontuacao, 'linha': linha, 'celulas_preenchidas': celulas_preenchidas})
    
    pontuacao_linhas.sort(key=lambda x: x['pontuacao'], reverse=True)
    resultado = pontuacao_linhas[0] if pontuacao_linhas and pontuacao_linhas[0]['pontuacao'] >= 2 else next((item for item in pontuacao_linhas if item['celulas_preenchidas'] >= 2), None)
    
    if not resultado:
        raise Exception("Não foi possível identificar os cabeçalhos na planilha.")
    
    header_row = resultado['indice']
    header_line = resultado['linha']
    
    colunas_mapeadas = {}
    linha = [str(cell).strip().lower() for cell in header_line]
    
    for tipo, palavras in colunas_padrao.items():
        melhor_coluna = None
        melhor_similaridade = 0
        for idx, celula in enumerate(linha):
            if not celula:
                continue
            for palavra in palavras:
                sim = similaridade(celula, palavra)
                if sim > melhor_similaridade:
                    melhor_similaridade = sim
                    melhor_coluna = idx
        if melhor_coluna is not None and melhor_similaridade > 0.5:
            colunas_mapeadas[tipo] = melhor_coluna
    
    if 'data' not in colunas_mapeadas or 'valor' not in colunas_mapeadas:
        raise Exception(f"Colunas obrigatórias não encontradas. Cabeçalhos: {', '.join([str(c) for c in header_line if c.strip()])}")
    
    df_dados = df_raw.iloc[header_row + 1:].copy().reset_index(drop=True)
    
    novo_nome = {idx: tipo for tipo, idx in colunas_mapeadas.items()}
    colunas_renomeadas = {col: novo_nome[idx] for idx, col in enumerate(df_dados.columns) if idx in novo_nome}
    df_processado = df_dados.rename(columns=colunas_renomeadas)[list(colunas_mapeadas.keys())]
    df_processado = df_processado.dropna(how='all')
    df_processado = df_processado[df_processado['data'].astype(str).str.strip() != '']
    df_processado = df_processado[df_processado['valor'].astype(str).str.strip() != '']
    df_processado = df_processado[df_processado['data'].astype(str).str.lower() != 'nan']
    df_processado = df_processado[df_processado['valor'].astype(str).str.lower() != 'nan']
    
    if df_processado.empty:
        raise Exception("Nenhum dado válido encontrado na planilha.")
    
    entries = []
    errors = []
    success_count = 0
    
    for idx, row in df_processado.iterrows():
        try:
            valor_data = row['data']
            try:
                transaction_date = pd.to_datetime(valor_data).date()
            except:
                for fmt in ['%d/%m/%Y', '%Y-%m-%d', '%d.%m.%Y']:
                    try:
                        transaction_date = datetime.strptime(str(valor_data), fmt).date()
                        break
                    except:
                        continue
                else:
                    errors.append(f"Linha {idx+2}: Data inválida - {valor_data}")
                    continue
            
            valor_str = re.sub(r'[R$MZN\s"\']', '', str(row['valor']).strip())
            if ',' in valor_str and '.' in valor_str:
                valor_str = valor_str.replace('.', '').replace(',', '.') if valor_str.rfind('.') > valor_str.rfind(',') else valor_str.replace(',', '.')
            elif ',' in valor_str:
                valor_str = valor_str.replace(',', '.')
            
            if not valor_str or valor_str.lower() in ['nan', 'null', 'none']:
                errors.append(f"Linha {idx+2}: Valor vazio")
                continue
            
            try:
                amount = abs(float(valor_str))
            except:
                errors.append(f"Linha {idx+2}: Valor inválido - {row['valor']}")
                continue
            
            if amount <= 0:
                errors.append(f"Linha {idx+2}: Valor deve ser maior que zero - {amount}")
                continue
            
            entry_type = 'IN'
            if 'tipo' in df_processado.columns and pd.notna(row.get('tipo')):
                tipo_str = str(row['tipo']).lower().strip()
                if any(p in tipo_str for p in ['entrada', 'receita', 'credito', 'in', 'positivo', '+']):
                    entry_type = 'IN'
                elif any(p in tipo_str for p in ['saida', 'despesa', 'debito', 'out', 'negativo', '-']):
                    entry_type = 'OUT'
            
            description = str(row['descricao'])[:255] if 'descricao' in df_processado.columns and pd.notna(row.get('descricao')) else f"Lançamento {success_count + 1}"
            category = str(row['categoria'])[:100] if 'categoria' in df_processado.columns and pd.notna(row.get('categoria')) else ''
            
            entries.append(CashFlowEntry(
                company=company, description=description, amount=Decimal(str(amount)),
                entry_type=entry_type, transaction_date=transaction_date, category=category, source='upload'
            ))
            success_count += 1
            
        except Exception as e:
            errors.append(f"Linha {idx+2}: {str(e)}")
            continue
    
    if success_count == 0:
        raise Exception(f"Nenhum dado válido encontrado. Erros: {'; '.join(errors[:5])}")
    
    if entries:
        CashFlowEntry.objects.bulk_create(entries, batch_size=500)
    
    return success_count

# ==================== EXPORTAÇÃO PARA EXCEL ====================
@login_required
def export_excel(request):
    """Exporta transações filtradas para Excel (.xlsx)"""
    company = request.user.profile.company
    
    # Obter filtros da requisição
    periodo = request.GET.get('periodo', '30')
    tipo = request.GET.get('tipo', '')
    categoria = request.GET.get('categoria', '')
    busca = request.GET.get('busca', '')
    
    # Base da query
    queryset = CashFlowEntry.objects.filter(company=company)
    
    # Filtrar por período
    dias = {'7': 7, '30': 30, '90': 90}.get(periodo, 30)
    queryset = queryset.filter(transaction_date__gte=timezone.now().date() - timedelta(days=dias))
    
    # Filtrar por tipo
    if tipo in ['IN', 'OUT']:
        queryset = queryset.filter(entry_type=tipo)
    
    # Filtrar por categoria
    if categoria:
        queryset = queryset.filter(category__icontains=categoria)
    
    # Buscar por descrição
    if busca:
        queryset = queryset.filter(Q(description__icontains=busca) | Q(category__icontains=busca))
    
    # Ordenar por data
    queryset = queryset.order_by('-transaction_date')
    
    # Calcular totais
    total_entradas = queryset.filter(entry_type='IN').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    total_saidas = queryset.filter(entry_type='OUT').aggregate(total=Sum('amount'))['total'] or Decimal('0')
    saldo = total_entradas - total_saidas
    
    # Criar DataFrame
    data = []
    for entry in queryset:
        data.append({
            'Data': entry.transaction_date.strftime('%d/%m/%Y'),
            'Descrição': entry.description,
            'Categoria': entry.category or '-',
            'Valor (MZN)': float(entry.amount),
            'Tipo': 'Entrada' if entry.entry_type == 'IN' else 'Saída',
            'Fonte': 'Upload' if entry.source == 'upload' else 'Manual'
        })
    
    df = pd.DataFrame(data)
    
    # Criar buffer para o Excel
    output = io.BytesIO()
    
    # Criar Excel com pandas
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Planilha principal com transações
        df.to_excel(writer, sheet_name='Transações', index=False)
        
        # Criar uma segunda planilha com resumo
        resumo_data = {
            'Indicador': ['Total de Entradas', 'Total de Saídas', 'Saldo Final', 'Total de Transações', 'Período'],
            'Valor': [
                f"{total_entradas:,.2f} MZN",
                f"{total_saidas:,.2f} MZN",
                f"{saldo:,.2f} MZN",
                len(queryset),
                f"Últimos {periodo} dias"
            ]
        }
        df_resumo = pd.DataFrame(resumo_data)
        df_resumo.to_excel(writer, sheet_name='Resumo', index=False)
        
        # Ajustar largura das colunas
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_length = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_length
    
    # Preparar resposta
    output.seek(0)
    filename = f'transacoes_{timezone.now().strftime("%Y%m%d_%H%M")}.xlsx'
    
    response = FileResponse(
        output,
        as_attachment=True,
        filename=filename,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response