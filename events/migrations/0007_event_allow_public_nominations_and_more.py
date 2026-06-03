from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0006_contactinquiry'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='allow_public_nominations',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='event',
            name='nomination_end_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='event',
            name='nomination_start_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
