import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth.models import User
from financas.models import Company, UserProfile  # <-- financas

# Criar empresa demo
company, created = Company.objects.get_or_create(
    name='Empresa Demo',
    tax_id='123456789',
    defaults={
        'phone': '841234567',
        'email': 'demo@empresa.com'
    }
)

# Criar usuário admin
if not User.objects.filter(username='admin').exists():
    user = User.objects.create_user(
        username='admin',
        password='admin123',
        email='admin@empresa.com',
        first_name='Administrador'
    )
    user.is_staff = True
    user.is_superuser = True
    user.save()
    
    # Vincular à empresa
    UserProfile.objects.get_or_create(
        user=user,
        defaults={
            'company': company,
            'role': 'admin'
        }
    )
    print('✅ Usuário admin criado com sucesso!')
    print('Usuário: admin')
    print('Senha: admin123')
else:
    print('⚠️ Usuário admin já existe')