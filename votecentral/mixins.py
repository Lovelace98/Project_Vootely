from django.contrib import messages
from django.db import IntegrityError


class SafeIntegrityMixin:
    """
    A mixin for CreateView and UpdateView that catches IntegrityError
    during the form save process. Instead of throwing a 500 server error,
    it adds a user-friendly error to the form and displays a message.
    """
    integrity_error_message = "This item cannot be saved because a similar record already exists."
    success_message = ""

    def form_valid(self, form):
        try:
            response = super().form_valid(form)
            if self.success_message:
                messages.success(self.request, self.success_message)
            return response
        except IntegrityError:
            messages.error(self.request, self.integrity_error_message)
            form.add_error(None, self.integrity_error_message)
            return self.form_invalid(form)
