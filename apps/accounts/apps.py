from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.accounts'  # Ekhane 'apps.' jog kora hoyeche


    def ready(self):
        import apps.accounts.signals