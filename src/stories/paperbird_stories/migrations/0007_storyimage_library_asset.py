from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stories", "0006_storyimage"),
        ("media_library", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="storyimage",
            name="library_asset",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="story_images",
                to="media_library.mediaasset",
                verbose_name="Медиа библиотеки",
            ),
        ),
    ]
