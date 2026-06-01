from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.forms import PasswordChangeForm
from allauth.account.forms import SignupForm

from notifications.phone import normalize_phone_number

from .models import User


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'phone_number', 'organizer_type', 'referral_source', 'avatar']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == 'avatar':
                field.widget.attrs['class'] = (
                    'file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 '
                    'file:text-xs file:font-semibold file:bg-vc-blue-50 file:text-vc-blue '
                    'hover:file:bg-vc-blue-100 dark:file:bg-slate-800 dark:file:text-white '
                    'cursor-pointer mt-1 block w-full text-xs text-vc-dark-400'
                )
            else:
                field.widget.attrs['class'] = (
                    'w-full rounded-2xl border border-vc-dark-200 bg-white px-4 py-3 '
                    'text-vc-dark focus:border-vc-blue focus:ring-vc-blue focus:outline-none '
                    'dark:border-vc-dark-600 dark:bg-vc-surface-raised dark:text-white'
                )

    def clean_phone_number(self):
        phone_number = self.cleaned_data.get('phone_number', '')
        if not phone_number:
            return ''
        return normalize_phone_number(phone_number, strict=True)

    def clean_avatar(self):
        avatar = self.cleaned_data.get('avatar')
        if avatar and hasattr(avatar, 'size'):
            max_size = 2 * 1024 * 1024  # 2MB
            if avatar.size > max_size:
                raise ValidationError('Avatar image must be under 2MB.')
        return avatar


class NotificationSettingsForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['phone_number', 'sms_opt_in']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['phone_number'].required = False
        self.fields['phone_number'].widget.attrs['placeholder'] = '+233241234567'
        for field in self.fields.values():
            field.widget.attrs['class'] = (
                'w-full rounded-2xl border border-vc-dark-200 bg-white px-4 py-3 '
                'text-vc-dark focus:border-vc-blue focus:ring-vc-blue focus:outline-none '
                'dark:border-vc-dark-600 dark:bg-vc-surface-raised dark:text-white'
            )

    def clean_phone_number(self):
        phone_number = self.cleaned_data.get('phone_number', '')
        return normalize_phone_number(phone_number, strict=True)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('sms_opt_in') and not cleaned_data.get('phone_number'):
            raise ValidationError('Add a phone number before enabling SMS notifications.')
        return cleaned_data


class CustomSignupForm(SignupForm):
    first_name = forms.CharField(
        max_length=30,
        label="First Name",
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. Ama',
        })
    )
    last_name = forms.CharField(
        max_length=30,
        label="Last Name",
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. Mensah',
        })
    )
    organizer_type = forms.ChoiceField(
        choices=User.OrganizerType.choices,
        label="I am registering as a",
        initial=User.OrganizerType.INDIVIDUAL,
    )
    referral_source = forms.ChoiceField(
        choices=[
            ('social_media', 'Social Media (Facebook, Twitter, etc.)'),
            ('search_engine', 'Search Engine (Google, Bing)'),
            ('friend', 'Friend / Colleague'),
            ('word_of_mouth', 'Word of Mouth'),
            ('other', 'Other'),
        ],
        label="How did you hear about VoteCentral?",
    )
    referral_source_other = forms.CharField(
        required=False,
        label="Specify other source (if applicable)",
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g., billboard, radio ad',
        })
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Apply custom visual styling to all form inputs
        for name, field in self.fields.items():
            classes = 'w-full rounded-2xl border border-vc-dark-200 bg-white px-4 py-3 text-vc-dark focus:border-vc-blue focus:ring-vc-blue focus:outline-none'
            field.widget.attrs['class'] = classes
        
        # Inject Alpine.js attributes for conditional rendering
        self.fields['referral_source'].widget.attrs['x-model'] = 'referral'

    def save(self, request):
        user = super().save(request)
        user.first_name = self.cleaned_data['first_name'].strip()
        user.last_name = self.cleaned_data['last_name'].strip()
        user.organizer_type = self.cleaned_data['organizer_type']
        
        ref = self.cleaned_data['referral_source']
        if ref == 'other' and self.cleaned_data.get('referral_source_other'):
            user.referral_source = self.cleaned_data['referral_source_other'].strip()
        else:
            user.referral_source = dict(self.fields['referral_source'].choices).get(ref, ref)
            
        user.save()
        return user

