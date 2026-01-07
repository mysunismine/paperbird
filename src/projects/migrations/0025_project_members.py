from django.conf import settings
from django.db import migrations, models


def add_owner_members(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    ProjectMember = apps.get_model("projects", "ProjectMember")
    for project in Project.objects.all():
        ProjectMember.objects.get_or_create(project_id=project.id, user_id=project.owner_id)


def remove_owner_members(apps, schema_editor):
    ProjectMember = apps.get_model("projects", "ProjectMember")
    ProjectMember.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0024_source_web_incremental_and_cache"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectMember",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Добавлен")),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="project_members",
                        to="projects.project",
                        verbose_name="Проект",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="project_memberships",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Пользователь",
                    ),
                ),
            ],
            options={
                "verbose_name": "Участник проекта",
                "verbose_name_plural": "Участники проектов",
            },
        ),
        migrations.AddField(
            model_name="project",
            name="members",
            field=models.ManyToManyField(
                blank=True,
                related_name="member_projects",
                through="projects.ProjectMember",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Участники",
            ),
        ),
        migrations.AddConstraint(
            model_name="projectmember",
            constraint=models.UniqueConstraint(
                fields=("project", "user"),
                name="unique_project_member",
            ),
        ),
        migrations.RunPython(add_owner_members, reverse_code=remove_owner_members),
    ]
