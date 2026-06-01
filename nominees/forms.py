from django import forms

from .models import Nominee


class NomineeForm(forms.ModelForm):
    class Meta:
        model = Nominee
        fields = ['name', 'bio', 'photo', 'display_order', 'is_active']
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 4}),
        }
        help_texts = {
            'photo': 'Recommended aspect ratio: 1:1 (Square). Optimal size: 500x500 pixels. Format: JPG, PNG.',
        }
        labels = {
            'photo': 'Nominee Photograph (1:1 Square)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'h-4 w-4 rounded border-slate-600 bg-slate-900 text-amber-500'
            else:
                field.widget.attrs['class'] = 'w-full rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-white'
