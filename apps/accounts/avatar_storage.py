import logging
from uuid import uuid4

import cloudinary
from cloudinary import uploader
from cloudinary.exceptions import Error as CloudinaryError
from django.conf import settings
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)


class AvatarStorageUnavailable(APIException):
    status_code = 503
    default_detail = 'Avatar storage is unavailable. Please try again later.'
    default_code = 'avatar_storage_unavailable'


def _configure_cloudinary():
    credentials = (
        settings.CLOUDINARY_CLOUD_NAME,
        settings.CLOUDINARY_API_KEY,
        settings.CLOUDINARY_API_SECRET,
    )
    if not all(credentials):
        raise AvatarStorageUnavailable(
            'Avatar storage is not configured. Please contact support.'
        )

    cloudinary.config(
        cloud_name=credentials[0],
        api_key=credentials[1],
        api_secret=credentials[2],
        secure=settings.CLOUDINARY_SECURE,
    )


def upload_avatar(image, user_id):
    _configure_cloudinary()
    public_id = f'user_{user_id}_{uuid4().hex}'

    try:
        result = uploader.upload(
            image,
            folder='docpoint/avatars',
            public_id=public_id,
            resource_type='image',
            allowed_formats=['jpg', 'jpeg', 'png', 'webp'],
            timeout=20,
        )
    except CloudinaryError as error:
        logger.exception('Cloudinary avatar upload failed for user %s.', user_id)
        raise AvatarStorageUnavailable() from error

    secure_url = result.get('secure_url')
    uploaded_public_id = result.get('public_id')
    if not isinstance(secure_url, str) or not isinstance(uploaded_public_id, str):
        raise AvatarStorageUnavailable('Avatar storage returned an invalid response.')

    return secure_url, uploaded_public_id


def delete_avatar(public_id, user_id):
    _configure_cloudinary()

    try:
        result = uploader.destroy(
            public_id,
            resource_type='image',
            invalidate=True,
            timeout=20,
        )
    except CloudinaryError as error:
        logger.exception('Cloudinary avatar deletion failed for user %s.', user_id)
        raise AvatarStorageUnavailable() from error

    if result.get('result') not in {'ok', 'not found'}:
        logger.error(
            'Cloudinary avatar deletion returned an unexpected result for user %s.',
            user_id,
        )
        raise AvatarStorageUnavailable()
