import django.db.models.deletion
from django.db import migrations, models


def backfill_nominee_categories(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    Nominee = apps.get_model('nominees', 'Nominee')
    CompetitionCategory = apps.get_model('nominees', 'CompetitionCategory')

    for event in Event.objects.filter(nominees__isnull=False).distinct():
        nominee_ids = list(
            Nominee.objects.filter(event_id=event.pk, category__isnull=True).values_list('id', flat=True)
        )
        if not nominee_ids:
            continue
        category = CompetitionCategory.objects.create(
            event_id=event.pk,
            name='General',
            slug='general',
            description='Auto-created default category for existing nominees.',
            display_order=0,
            is_active=True,
        )
        Nominee.objects.filter(id__in=nominee_ids).update(category_id=category.pk)


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0007_event_allow_public_nominations_and_more'),
        ('nominees', '0002_nominee_email_nominee_phone_number'),
    ]

    operations = [
        migrations.CreateModel(
            name='CompetitionCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('slug', models.SlugField(blank=True, max_length=160)),
                ('description', models.TextField(blank=True)),
                ('display_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='competition_categories', to='events.event')),
            ],
            options={
                'ordering': ('display_order', 'name'),
                'constraints': [models.UniqueConstraint(fields=('event', 'slug'), name='unique_category_slug_per_event')],
            },
        ),
        migrations.AddField(
            model_name='nominee',
            name='category',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='nominees', to='nominees.competitioncategory'),
        ),
        migrations.CreateModel(
            name='NominationSubmission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=160)),
                ('bio', models.TextField(blank=True)),
                ('photo', models.ImageField(blank=True, upload_to='nomination-submissions/photos/')),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('phone_number', models.CharField(blank=True, max_length=20)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending', max_length=16)),
                ('review_notes', models.TextField(blank=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='nomination_submissions', to='nominees.competitioncategory')),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='nomination_submissions', to='events.event')),
            ],
            options={
                'ordering': ('-created_at',),
                'indexes': [
                    models.Index(fields=['event', 'status', '-created_at'], name='nomsub_event_status_created'),
                    models.Index(fields=['category', 'status', '-created_at'], name='nomsub_cat_status_created'),
                ],
            },
        ),
        migrations.RunPython(backfill_nominee_categories, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='nominee',
            name='category',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='nominees', to='nominees.competitioncategory'),
        ),
        migrations.AddField(
            model_name='nominationsubmission',
            name='approved_nominee',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='source_submission', to='nominees.nominee'),
        ),
    ]
