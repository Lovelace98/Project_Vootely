from django import forms

from .models import Event


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
            'description': forms.Textarea(attrs={'rows': 5}),
        }
        help_texts = {
            'banner': 'Landscape view. Recommended size: 1200x630 pixels (Aspect ratio 16:9). Used for detail pages.',
            'flyer': 'Square view. Recommended size: 1080x1080 pixels (Aspect ratio 1:1). Used for listings and card grids.',
            'show_leaderboard': 'Hide the results from the public until you are ready to reveal them.',
            'is_public': 'Make this event visible on the public listing page.',
        }
        labels = {
            'banner': 'Event Banner (Landscape)',
            'flyer': 'Event Flyer (1:1 Square)',
            'show_leaderboard': 'Show Leaderboard Publicly',
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
