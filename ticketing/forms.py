from django import forms
from django.contrib.auth.hashers import make_password

from .models import TicketScannerPass, TicketType, scanner_pass_default_expiry


class TicketTypeForm(forms.ModelForm):
    class Meta:
        model = TicketType
        fields = [
            'name',
            'description',
            'price',
            'quantity_available',
            'sale_start_at',
            'sale_end_at',
            'max_per_order',
            'is_active',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'sale_start_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'sale_end_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        }

    def __init__(self, *args, **kwargs):
        self.event = kwargs.pop('event', None)
        super().__init__(*args, **kwargs)
        self.fields['sale_start_at'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['sale_end_at'].input_formats = ['%Y-%m-%dT%H:%M']

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.event is not None:
            instance.event = self.event
        if commit:
            instance.save()
        return instance

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if not name:
            return name
        queryset = TicketType.objects.filter(event=self.event, name__iexact=name)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if self.event is not None and queryset.exists():
            raise forms.ValidationError('A ticket type with this name already exists for this event.')
        return name


class TicketPurchaseForm(forms.Form):
    event_slug = forms.SlugField(widget=forms.HiddenInput())
    ticket_type_id = forms.IntegerField(widget=forms.HiddenInput())
    quantity = forms.IntegerField(min_value=1, initial=1)
    buyer_name = forms.CharField(max_length=120, required=False)
    buyer_email = forms.EmailField(required=True)
    buyer_phone = forms.CharField(max_length=32, required=False)


class TicketManualCheckInForm(forms.Form):
    code = forms.CharField(max_length=32)


class TicketScannerPassForm(forms.ModelForm):
    pin = forms.CharField(
        min_length=4,
        max_length=12,
        widget=forms.PasswordInput(),
        help_text='Share this PIN with the staff member separately from the scanner link.',
    )

    class Meta:
        model = TicketScannerPass
        fields = ['gate_name', 'staff_label', 'pin']

    def __init__(self, *args, **kwargs):
        self.event = kwargs.pop('event')
        self.created_by = kwargs.pop('created_by', None)
        super().__init__(*args, **kwargs)

    def clean_gate_name(self):
        return (self.cleaned_data.get('gate_name') or '').strip()

    def clean_staff_label(self):
        return (self.cleaned_data.get('staff_label') or '').strip()

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.event = self.event
        instance.created_by = self.created_by
        instance.pin_hash = make_password(self.cleaned_data['pin'])
        instance.expires_at = scanner_pass_default_expiry(self.event)
        if commit:
            instance.save()
        return instance


class TicketScannerCredentialResetForm(forms.Form):
    pin = forms.CharField(
        min_length=4,
        max_length=12,
        widget=forms.PasswordInput(),
        help_text='Set a new PIN for the staff member. The scanner link will also change.',
    )


class TicketScannerActivationForm(forms.Form):
    pin = forms.CharField(min_length=4, max_length=12, widget=forms.PasswordInput())
