from django.conf import settings

def paystack_settings(request):
    return {
        'PAYSTACK_PUBLIC_KEY': settings.PAYSTACK_PUBLIC_KEY,
    }
