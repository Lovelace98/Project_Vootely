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


class ElectionPositionForm(StyledModelForm):
    class Meta:
        model = ElectionPosition
        fields = ['title', 'max_choices', 'display_order', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_styles()


class ElectionCandidateForm(StyledModelForm):
    class Meta:
        model = ElectionCandidate
        fields = ['position', 'name', 'bio', 'photo', 'display_order', 'is_active']
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        if event is not None:
            self.fields['position'].queryset = event.election_positions.filter(is_active=True)
        self._apply_styles()


class RosterUploadForm(forms.Form):
    roster = forms.FileField(help_text='CSV with external_id, name, email, phone headers.')


class CredentialTokenForm(forms.Form):
    token = forms.CharField(max_length=200)
