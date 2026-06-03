from django import forms

from .models import ContactInquiry, Event


class StyledModelForm(forms.ModelForm):
    def _apply_styles(self):
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'h-5 w-5 rounded border-slate-300 bg-white text-vc-blue focus:ring-vc-blue'
            else:
                # Use global styles from src.css for other inputs
                pass


class EventForm(StyledModelForm):
    class Meta:
        model = Event
        fields = [
            'title',
            'description',
            'banner',
            'flyer',
            'currency',
            'vote_price',
            'start_at',
            'end_at',
            'is_public',
            'show_leaderboard',
            'allow_public_nominations',
            'nomination_start_at',
            'nomination_end_at',
        ]
        widgets = {
            'start_at': forms.DateTimeInput(
                attrs={'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'end_at': forms.DateTimeInput(
                attrs={'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'nomination_start_at': forms.DateTimeInput(
                attrs={'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'nomination_end_at': forms.DateTimeInput(
                attrs={'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'description': forms.Textarea(attrs={'rows': 5}),
        }
        help_texts = {
            'banner': 'Landscape view. Recommended size: 1200x630 pixels (Aspect ratio 16:9). Used for detail pages. File size must be less than 2MB.',
            'flyer': 'Square view. Recommended size: 1080x1080 pixels (Aspect ratio 1:1). Used for listings and card grids. File size must be less than 2MB.',
            'show_leaderboard': 'Hide the results from the public until you are ready to reveal them.',
            'is_public': 'Make this event visible on the public listing page.',
            'allow_public_nominations': 'Allow people to submit themselves for a category using a public nomination link.',
            'nomination_start_at': 'When public self-nominations open.',
            'nomination_end_at': 'When public self-nominations close.',
        }
        labels = {
            'banner': 'Event Banner (Landscape)',
            'flyer': 'Event Flyer (1:1 Square)',
            'show_leaderboard': 'Show Leaderboard Publicly',
            'allow_public_nominations': 'Enable Public Self-Nominations',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_styles()
        self.fields['start_at'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['end_at'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['nomination_start_at'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['nomination_end_at'].input_formats = ['%Y-%m-%dT%H:%M']

    def clean(self):
        cleaned_data = super().clean()
        start_at = cleaned_data.get('start_at')
        end_at = cleaned_data.get('end_at')
        if start_at and end_at and end_at <= start_at:
            raise forms.ValidationError('End date must be after the start date.')
        return cleaned_data

    def clean_banner(self):
        banner = self.cleaned_data.get('banner')
        if banner and hasattr(banner, 'size'):
            if banner.size > 2 * 1024 * 1024:
                raise forms.ValidationError('Banner image size must be less than 2MB.')
        return banner

    def clean_flyer(self):
        flyer = self.cleaned_data.get('flyer')
        if flyer and hasattr(flyer, 'size'):
            if flyer.size > 2 * 1024 * 1024:
                raise forms.ValidationError('Flyer image size must be less than 2MB.')
        return flyer


class ContactInquiryForm(StyledModelForm):
    class Meta:
        model = ContactInquiry
        fields = ['name', 'email', 'phone_number', 'heard_about_us', 'message']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Your full name'}),
            'email': forms.EmailInput(attrs={'placeholder': 'you@example.com'}),
            'phone_number': forms.TextInput(attrs={'placeholder': '+233 24 000 0000'}),
            'heard_about_us': forms.Select(),
            'message': forms.Textarea(
                attrs={
                    'rows': 5,
                    'placeholder': 'Tell us what you want to run or ask.',
                }
            ),
        }
        labels = {
            'heard_about_us': 'Where did you hear about us?',
            'message': 'Question or message',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_styles()
        self.fields['heard_about_us'].choices = [
            ('', 'Select one'),
            *ContactInquiry.HeardAboutUs.choices,
        ]
