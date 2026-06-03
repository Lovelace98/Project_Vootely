from django import forms

from events.forms import StyledModelForm
from events.models import Event

from .models import ElectionCandidate, ElectionPosition


class ElectionEventForm(StyledModelForm):
    class Meta:
        model = Event
        fields = [
            'title',
            'description',
            'banner',
            'flyer',
            'currency',
            'start_at',
            'end_at',
            'is_public',
        ]
        widgets = {
            'start_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'end_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'description': forms.Textarea(attrs={'rows': 5}),
        }
        help_texts = {
            'is_public': 'Required before voters can access the election page.',
            'banner': 'Landscape view. Recommended size: 1200x630 pixels (Aspect ratio 16:9). Used for detail pages. File size must be less than 2MB.',
            'flyer': 'Square view. Recommended size: 1080x1080 pixels (Aspect ratio 1:1). Used for listings and card grids. File size must be less than 2MB.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_styles()
        self.fields['start_at'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['end_at'].input_formats = ['%Y-%m-%dT%H:%M']

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


class ElectionPositionForm(StyledModelForm):
    class Meta:
        model = ElectionPosition
        fields = ['title', 'max_choices', 'display_order', 'is_active']

    def __init__(self, *args, event=None, **kwargs):
        self.event = event
        super().__init__(*args, **kwargs)
        self._apply_styles()

    def clean_title(self):
        title = (self.cleaned_data.get('title') or '').strip()
        event = self.event or getattr(self.instance, 'event', None)
        if event and title:
            duplicate_qs = ElectionPosition.objects.exclude(pk=self.instance.pk).filter(
                event=event,
                title__iexact=title,
            )
            if duplicate_qs.exists():
                raise forms.ValidationError('A position with this title already exists for this election.')
        return title


class ElectionCandidateForm(StyledModelForm):
    class Meta:
        model = ElectionCandidate
        fields = ['position', 'name', 'bio', 'photo', 'email', 'phone', 'display_order', 'is_active']
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 4}),
        }
        help_texts = {
            'photo': 'Recommended aspect ratio: 1:1 (Square). Format: JPG, PNG. File size must be less than 2MB.',
        }

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        if event is not None:
            self.fields['position'].queryset = event.election_positions.filter(is_active=True)
        self._apply_styles()

    def clean_photo(self):
        photo = self.cleaned_data.get('photo')
        if photo and hasattr(photo, 'size'):
            if photo.size > 2 * 1024 * 1024:
                raise forms.ValidationError('Photo size must be less than 2MB.')
        return photo

    def clean(self):
        cleaned_data = super().clean()
        position = cleaned_data.get('position')
        name = (cleaned_data.get('name') or '').strip()
        email = (cleaned_data.get('email') or '').strip().lower()
        phone = (cleaned_data.get('phone') or '').strip()
        if position and name:
            duplicate_qs = ElectionCandidate.objects.exclude(pk=self.instance.pk).filter(
                position=position,
                name__iexact=name,
            )
            is_duplicate = False
            for dup in duplicate_qs:
                dup_email = dup.email.strip().lower()
                dup_phone = dup.phone.strip()
                
                email_match = email and dup_email and email == dup_email
                phone_match = phone and dup_phone and phone == dup_phone
                both_empty = not email and not phone and not dup_email and not dup_phone
                
                if email_match or phone_match or both_empty:
                    is_duplicate = True
                    break
            
            if is_duplicate:
                self.add_error('name', 'A candidate with this name and contact details already exists for the selected position.')
        return cleaned_data


class RosterUploadForm(forms.Form):
    roster = forms.FileField(help_text='CSV with external_id, name, email, phone headers.')


class CredentialTokenForm(forms.Form):
    token = forms.CharField(max_length=200)
