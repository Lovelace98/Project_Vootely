from django import forms


class PaymentInitiationForm(forms.Form):
    event_slug = forms.SlugField(widget=forms.HiddenInput())
    nominee_ref = forms.CharField(widget=forms.HiddenInput())
    quantity = forms.IntegerField(min_value=1, initial=1)
    voter_name = forms.CharField(max_length=120, required=False)
    voter_email = forms.EmailField(required=True)
    voter_phone = forms.CharField(max_length=32, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.HiddenInput):
                continue
            field.widget.attrs['class'] = 'w-full rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-white'
