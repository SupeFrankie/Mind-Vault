from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)  # Django's hasher; we never touch crypto ourselves.
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self._create(email, password, **extra)


class User(AbstractUser):
    """Custom user, defined before the first migration. Email is the login id.

    MySQL's default utf8mb4 collation is case-insensitive, so the unique index
    treats Foo@x.com and foo@x.com as the same address; we also lower-case on save.
    """

    username = None
    email = models.EmailField("email address", unique=True)
    full_name = models.CharField(max_length=150)
    university = models.CharField(max_length=150, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]
    objects = UserManager()

    # AbstractUser's first_name/last_name are unused; full_name replaces them.
    first_name = None
    last_name = None

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.email

    def save(self, *args, **kwargs):
        self.email = self.email.lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email
