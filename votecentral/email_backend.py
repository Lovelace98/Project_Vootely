from django.core.mail.backends.console import EmailBackend as ConsoleEmailBackend


class ReadableConsoleEmailBackend(ConsoleEmailBackend):
    def write_message(self, message):
        self.stream.write("\n" + "═"*40 + "\n")
        self.stream.write(f"Subject: {message.subject}\n")
        self.stream.write(f"From: {message.from_email}\n")
        self.stream.write(f"To: {', '.join(message.to)}\n\n")
        
        # Write the unencoded clean plain text body directly
        self.stream.write(message.body)
        self.stream.write("\n" + "═"*42 + "\n")
        self.stream.flush()
