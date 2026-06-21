from django import forms
from django.db.models import Q

from .models import CompetitionCategory, NominationSubmission, Nominee


class NomineeForm(forms.ModelForm):
    class Meta:
        model = Nominee
        fields = ['category', 'name', 'bio', 'photo', 'email', 'phone_number', 'display_order', 'is_active']
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 4}),
        }
        help_texts = {
            'photo': 'Recommended aspect ratio: 1:1 (Square). Optimal size: 500x500 pixels. Format: JPG, PNG. File size must be less than 2MB.',
        }
        labels = {
            'category': 'Competition Category',
            'photo': 'Nominee Photograph (1:1 Square)',
        }

    def clean_photo(self):
        photo = self.cleaned_data.get('photo')
        if photo and hasattr(photo, 'size'):
            if photo.size > 2 * 1024 * 1024:
                raise forms.ValidationError('Photo size must be less than 2MB.')
        return photo

    def __init__(self, *args, **kwargs):
        self.event = kwargs.pop('event', None)
        super().__init__(*args, **kwargs)
        if self.event is not None:
            self.fields['category'].queryset = CompetitionCategory.objects.filter(event=self.event).order_by('display_order', 'name')
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'h-4 w-4 rounded border-slate-600 bg-slate-900 text-amber-500'
            else:
                field.widget.attrs['class'] = 'w-full rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-white'

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get('category')
        name = (cleaned_data.get('name') or '').strip()
        email = (cleaned_data.get('email') or '').strip().lower()
        phone_number = (cleaned_data.get('phone_number') or '').strip()
        event = self.event or getattr(self.instance, 'event', None)
        if event and category and name:
            duplicate_qs = Nominee.objects.exclude(pk=self.instance.pk).filter(
                event=event,
                category=category,
                name__iexact=name,
            )
            duplicate_query = Q()
            if email:
                duplicate_query |= Q(email__iexact=email)
            if phone_number:
                duplicate_query |= Q(phone_number=phone_number)
            if not email and not phone_number:
                duplicate_query = Q(email='', phone_number='')
            if duplicate_qs.filter(duplicate_query).exists():
                self.add_error('name', 'A nominee with this name and contact details already exists in the selected category.')
        return cleaned_data


class CompetitionCategoryForm(forms.ModelForm):
    class Meta:
        model = CompetitionCategory
        fields = ['name', 'description', 'display_order', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, event=None, **kwargs):
        self.event = event
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'h-4 w-4 rounded border-slate-600 bg-slate-900 text-amber-500'
            else:
                field.widget.attrs['class'] = 'w-full rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-white'

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        event = self.event or getattr(self.instance, 'event', None)
        if event and name:
            duplicate_qs = CompetitionCategory.objects.exclude(pk=self.instance.pk).filter(
                event=event,
                name__iexact=name,
            )
            if duplicate_qs.exists():
                raise forms.ValidationError('A category with this name already exists for this event.')
        return name


class NominationSubmissionForm(forms.ModelForm):
    class Meta:
        model = NominationSubmission
        fields = ['category', 'name', 'bio', 'photo', 'email', 'phone_number']
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 4}),
        }
        labels = {
            'category': 'Category',
            'photo': 'Your Photograph (1:1 Square)',
        }
        help_texts = {
            'photo': 'Recommended aspect ratio: 1:1 (Square). Optimal size: 500x500 pixels. Format: JPG, PNG. File size must be less than 2MB.',
        }

    def clean_photo(self):
        photo = self.cleaned_data.get('photo')
        if photo and hasattr(photo, 'size'):
            if photo.size > 2 * 1024 * 1024:
                raise forms.ValidationError('Photo size must be less than 2MB.')
        return photo

    def __init__(self, *args, **kwargs):
        self.event = kwargs.pop('event', None)
        super().__init__(*args, **kwargs)
        if self.event is not None:
            self.instance.event = self.event
            self.fields['category'].queryset = CompetitionCategory.objects.filter(
                event=self.event,
                is_active=True,
            ).order_by('display_order', 'name')
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'h-4 w-4 rounded border-slate-600 bg-slate-900 text-amber-500'
            else:
                field.widget.attrs['class'] = 'w-full rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-white'


class NominationReviewForm(forms.ModelForm):
    display_order = forms.IntegerField(min_value=0, initial=0)
    is_active = forms.BooleanField(required=False, initial=True)

    class Meta:
        model = NominationSubmission
        fields = ['category', 'name', 'bio', 'photo', 'email', 'phone_number', 'review_notes']
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 4}),
            'review_notes': forms.Textarea(attrs={'rows': 3}),
        }
        help_texts = {
            'photo': 'Recommended aspect ratio: 1:1 (Square). Optimal size: 500x500 pixels. Format: JPG, PNG. File size must be less than 2MB.',
        }

    def clean_photo(self):
        photo = self.cleaned_data.get('photo')
        if photo and hasattr(photo, 'size'):
            if photo.size > 2 * 1024 * 1024:
                raise forms.ValidationError('Photo size must be less than 2MB.')
        return photo

    def __init__(self, *args, **kwargs):
        self.event = kwargs.pop('event', None)
        super().__init__(*args, **kwargs)
        if self.event is not None:
            self.fields['category'].queryset = CompetitionCategory.objects.filter(event=self.event).order_by('display_order', 'name')
        if self.instance and self.instance.approved_nominee_id:
            self.fields['display_order'].initial = self.instance.approved_nominee.display_order
            self.fields['is_active'].initial = self.instance.approved_nominee.is_active
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'h-4 w-4 rounded border-slate-600 bg-slate-900 text-amber-500'
            else:
                field.widget.attrs['class'] = 'w-full rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-white'

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get('category')
        name = (cleaned_data.get('name') or '').strip()
        email = (cleaned_data.get('email') or '').strip().lower()
        phone_number = (cleaned_data.get('phone_number') or '').strip()
        event = self.event or getattr(self.instance, 'event', None)
        if event and category and name:
            exclude_pk = getattr(self.instance, 'approved_nominee_id', None)
            duplicate_qs = Nominee.objects.exclude(pk=exclude_pk).filter(
                event=event,
                category=category,
                name__iexact=name,
            )
            duplicate_query = Q()
            if email:
                duplicate_query |= Q(email__iexact=email)
            if phone_number:
                duplicate_query |= Q(phone_number=phone_number)
            if not email and not phone_number:
                duplicate_query = Q(email='', phone_number='')
            if duplicate_qs.filter(duplicate_query).exists():
                self.add_error('name', 'A nominee with this name and contact details already exists in the selected category.')
        return cleaned_data
