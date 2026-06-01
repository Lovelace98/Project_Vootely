import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class ComplexityValidator:
    def __init__(self, min_digits=1, min_uppercase=1, min_symbols=1):
        self.min_digits = min_digits
        self.min_uppercase = min_uppercase
        self.min_symbols = min_symbols

    def validate(self, password, user=None):
        if self.min_uppercase and len(re.findall(r'[A-Z]', password)) < self.min_uppercase:
            raise ValidationError(
                _("This password must contain at least %(min_uppercase)d uppercase letter(s)."),
                code='password_no_uppercase',
                params={'min_uppercase': self.min_uppercase},
            )
        if self.min_digits and len(re.findall(r'[0-9]', password)) < self.min_digits:
            raise ValidationError(
                _("This password must contain at least %(min_digits)d digit(s)."),
                code='password_no_digit',
                params={'min_digits': self.min_digits},
            )
        if self.min_symbols and len(re.findall(r'[^\w\s]', password)) < self.min_symbols:
            raise ValidationError(
                _("This password must contain at least %(min_symbols)d special character(s) (e.g. !@#$%%^&*)."),
                code='password_no_symbol',
                params={'min_symbols': self.min_symbols},
            )

    def get_help_text(self):
        return _(
            "Your password must contain at least 1 uppercase letter, 1 digit, and 1 special character."
        )
