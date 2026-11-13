from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import PatientProfile


DEMO_PATIENTS = (
    ("Alberto", "Ripley", "alberto.ripley", "1990-01-25", "MALE", "O+", "+1 54546 45648", "4150 Hiney Road, Las Vegas, Nevada 89109"),
    ("Susan", "Babin", "susan.babin", "1994-06-12", "FEMALE", "A+", "+1 54554 54789", "26 Hill Road, Austin, Texas 78701"),
    ("Carol", "Lam", "carol.lam", "1997-03-08", "FEMALE", "B+", "+1 43554 54985", "318 Pine Street, Seattle, Washington 98101"),
    ("Marsha", "Noland", "marsha.noland", "1990-09-17", "FEMALE", "AB+", "+1 47554 54257", "12 Lake Street, Chicago, Illinois 60601"),
    ("Irma", "Armstrong", "irma.armstrong", "1993-02-14", "FEMALE", "O-", "+1 54114 57526", "74 Central Avenue, Phoenix, Arizona 85001"),
    ("Jesus", "Adams", "jesus.adams", "1998-04-22", "MALE", "A-", "+1 51247 56574", "9 Peachtree Street, Atlanta, Georgia 30303"),
    ("Ezra", "Belcher", "ezra.belcher", "1997-11-03", "MALE", "B-", "+1 41452 25741", "85 Ocean Drive, Miami, Florida 33101"),
    ("Glen", "Lentz", "glen.lentz", "2003-05-09", "MALE", "O+", "+1 51425 21498", "44 Union Street, Denver, Colorado 80201"),
    ("Bernard", "Griffith", "bernard.griffith", "1991-07-30", "MALE", "A+", "+1 45214 98741", "70 Beacon Street, Boston, Massachusetts 02108"),
    ("John", "Elsass", "john.elsass", "2002-01-19", "MALE", "AB-", "+1 41245 32540", "21 Orange Avenue, Orlando, Florida 32801"),
    ("Maya", "Bennett", "maya.bennett", "1988-03-11", "FEMALE", "A+", "+1 41520 31940", "11 Market Street, San Francisco, California 94103"),
    ("Lucas", "Morgan", "lucas.morgan", "1985-08-02", "MALE", "B+", "+1 41520 31941", "22 Sunset Boulevard, Los Angeles, California 90012"),
    ("Nora", "Foster", "nora.foster", "1996-12-07", "FEMALE", "O-", "+1 41520 31942", "33 Main Street, Dallas, Texas 75201"),
    ("Ethan", "Brooks", "ethan.brooks", "1992-05-19", "MALE", "AB+", "+1 41520 31943", "44 First Avenue, Houston, Texas 77002"),
    ("Ava", "Parker", "ava.parker", "2000-10-28", "FEMALE", "B-", "+1 41520 31944", "55 Broad Street, New York, New York 10004"),
    ("Noah", "Reed", "noah.reed", "1987-02-16", "MALE", "A-", "+1 41520 31945", "66 King Street, Charleston, South Carolina 29401"),
    ("Lily", "Hayes", "lily.hayes", "1999-07-05", "FEMALE", "O+", "+1 41520 31946", "77 State Street, Madison, Wisconsin 53703"),
    ("Owen", "Price", "owen.price", "1983-11-23", "MALE", "AB-", "+1 41520 31947", "88 Pearl Street, Portland, Oregon 97204"),
    ("Zoe", "Ward", "zoe.ward", "1995-04-30", "FEMALE", "A+", "+1 41520 31948", "99 Congress Avenue, Austin, Texas 78701"),
    ("Leo", "Cole", "leo.cole", "2001-09-14", "MALE", "B+", "+1 41520 31949", "100 River Road, Nashville, Tennessee 37201"),
)


class Command(BaseCommand):
    help = "Create or update 20 demo patient accounts and profiles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-remote",
            action="store_true",
            help="Confirm that demo patient records should be written to a remote database.",
        )

    def handle(self, *args, **options):
        database_host = (settings.DATABASES["default"].get("HOST") or "").strip().lower()
        is_remote_database = database_host not in {
            "",
            "localhost",
            "127.0.0.1",
            "::1",
            "[::1]",
        }
        if (not settings.DEBUG or is_remote_database) and not options["allow_remote"]:
            raise CommandError(
                "This is not a local debug database. To seed it intentionally, rerun "
                "with --allow-remote."
            )

        user_model = get_user_model()
        created_count = 0
        updated_count = 0

        with transaction.atomic():
            for first_name, last_name, email_prefix, dob, gender, blood_group, phone, address in DEMO_PATIENTS:
                email = f"{email_prefix}@demo-patients.docpoint.test"
                user = user_model.objects.filter(email=email).first()

                if user and user.role != user_model.Role.PATIENT:
                    raise CommandError(
                        f"{email} already exists with role {user.role}; expected PATIENT."
                    )

                if user is None:
                    user = user_model(
                        email=email,
                        role=user_model.Role.PATIENT,
                        first_name=first_name,
                        last_name=last_name,
                        phone_number=phone,
                        is_active=True,
                        is_verified=True,
                    )
                    user.set_unusable_password()
                    user.save()
                    created_count += 1
                else:
                    user.first_name = first_name
                    user.last_name = last_name
                    user.phone_number = phone
                    user.is_active = True
                    user.is_verified = True
                    user.save(
                        update_fields=[
                            "first_name",
                            "last_name",
                            "phone_number",
                            "is_active",
                            "is_verified",
                        ]
                    )
                    updated_count += 1

                PatientProfile.objects.update_or_create(
                    user=user,
                    defaults={
                        "date_of_birth": date.fromisoformat(dob),
                        "gender": gender,
                        "blood_group": blood_group,
                        "address": address,
                    },
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo patients ready: {len(DEMO_PATIENTS)} "
                f"({created_count} created, {updated_count} updated)."
            )
        )
