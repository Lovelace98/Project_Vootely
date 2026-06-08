from django import forms

from .models import ContactInquiry, Event, VoteBundle


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


class TicketedEventForm(StyledModelForm):
    class Meta:
        model = Event
        fields = [
            'title',
            'description',
            'venue',
            'event_date',
            'banner',
            'flyer',
            'currency',
            'start_at',
            'end_at',
            'is_public',
        ]
        widgets = {
            'event_date': forms.DateTimeInput(
                attrs={'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
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
        labels = {
            'banner': 'Event Banner (Landscape)',
            'flyer': 'Event Flyer (1:1 Square)',
            'event_date': 'Event Date & Time',
            'start_at': 'Ticket Sale Starts',
            'end_at': 'Ticket Sale Ends',
            'venue': 'Venue / Location',
        }
        help_texts = {
            'banner': 'Landscape view. Recommended size: 1200x630 pixels.',
            'flyer': 'Square view. Recommended size: 1080x1080 pixels.',
            'is_public': 'Make this event visible on the public listing page after publishing.',
            'event_date': 'When does the event take place? Leave blank if not yet confirmed.',
            'venue': 'Where is the event? Leave blank if the venue hasn\'t been confirmed yet.',
            'start_at': 'When should ticket sales begin?',
            'end_at': 'When should ticket sales close?',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_styles()
        if 'event_date' in self.fields:
            self.fields['event_date'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['start_at'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['end_at'].input_formats = ['%Y-%m-%dT%H:%M']

    def clean(self):
        cleaned_data = super().clean()
        start_at = cleaned_data.get('start_at')
        end_at = cleaned_data.get('end_at')
        if start_at and end_at and end_at <= start_at:
            raise forms.ValidationError('End date must be after the start date.')
        return cleaned_data


class ContactInquiryForm(StyledModelForm):
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                'style': 'display:none !important;',
                'tabindex': '-1',
                'autocomplete': 'off',
            }
        ),
        label='',
    )

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

    def clean(self):
        cleaned_data = super().clean()
        website = cleaned_data.get('website')
        if website:
            raise forms.ValidationError("Spam request blocked.")
        return cleaned_data


class VoteBundleForm(StyledModelForm):
    class Meta:
        model = VoteBundle
        fields = ['quantity', 'price', 'label', 'is_active']
        widgets = {
            'quantity': forms.NumberInput(attrs={'min': 1, 'class': 'w-full rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950 px-4 py-3 text-vc-dark dark:text-white', 'placeholder': 'e.g. 50'}),
            'price': forms.NumberInput(attrs={'min': 0.01, 'step': 0.01, 'class': 'w-full rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950 px-4 py-3 text-vc-dark dark:text-white', 'placeholder': 'e.g. 40.00'}),
            'label': forms.TextInput(attrs={'class': 'w-full rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950 px-4 py-3 text-vc-dark dark:text-white', 'placeholder': 'e.g. Save 10%, Popular'}),
        }
        help_texts = {
            'quantity': 'Number of votes bundled (e.g. 50)',
            'price': 'Discounted package price (e.g. GHS 40.00 instead of GHS 50.00)',
            'label': 'Badge label shown to users (optional)',
        }

    def __init__(self, *args, **kwargs):
        self.event = kwargs.pop('event', None)
        super().__init__(*args, **kwargs)
        self._apply_styles()

    def clean(self):
        cleaned_data = super().clean()
        qty = cleaned_data.get('quantity')
        price = cleaned_data.get('price')
        if qty and price and self.event:
            base_price = self.event.vote_price or 1.00
            unit_price = price / qty
            if unit_price > base_price:
                raise forms.ValidationError(
                    f"Bundle unit price ({unit_price:.2f}) cannot exceed the event's base vote price ({base_price:.2f})."
                )
        return cleaned_data
