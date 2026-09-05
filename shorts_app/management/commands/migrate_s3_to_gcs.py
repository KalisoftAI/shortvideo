import os
import logging
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "One-time migration helper: copy existing media from an AWS S3 bucket into "
        "Google Cloud Storage. Reads the source bucket and credentials from "
        "environment variables (AWS_MIGRATION_ACCESS_KEY_ID, "
        "AWS_MIGRATION_SECRET_ACCESS_KEY, AWS_MIGRATION_BUCKET). "
        "Supports --dry-run and never deletes S3 objects."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List objects that would be copied without performing the copy.',
        )
        parser.add_argument(
            '--prefix',
            default='',
            help='Only migrate objects under this S3 key prefix (e.g. "yt_video/").',
        )
        parser.add_argument(
            '--max',
            type=int,
            default=None,
            help='Maximum number of objects to migrate (for a limited test run).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        prefix = options['prefix']
        max_objects = options['max']

        source_bucket = os.environ.get('AWS_MIGRATION_BUCKET')
        access_key = os.environ.get('AWS_MIGRATION_ACCESS_KEY_ID')
        secret_key = os.environ.get('AWS_MIGRATION_SECRET_ACCESS_KEY')
        region = os.environ.get('AWS_MIGRATION_REGION', 'us-east-1')

        if not (source_bucket and access_key and secret_key):
            raise CommandError(
                "AWS_MIGRATION_BUCKET, AWS_MIGRATION_ACCESS_KEY_ID and "
                "AWS_MIGRATION_SECRET_ACCESS_KEY environment variables are required."
            )

        try:
            import boto3
        except ImportError:
            raise CommandError(
                "boto3 is required to run this migration command. "
                "Install it temporarily with: pip install boto3"
            )

        if dry_run:
            self.stdout.write("DRY RUN MODE - no objects will be copied.\n")

        s3 = boto3.client(
            's3',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

        paginator = s3.get_paginator('list_objects_v2')
        copied = 0
        skipped = 0
        failed = 0

        for page in paginator.paginate(Bucket=source_bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if max_objects is not None and copied >= max_objects:
                    self.stdout.write(f"Reached --max limit of {max_objects} objects.")
                    break

                try:
                    existing = default_storage.exists(key)
                    if existing:
                        skipped += 1
                        self.stdout.write(f"[skip] already in GCS: {key}")
                        continue

                    if dry_run:
                        self.stdout.write(f"[dry-run] would copy: {key}")
                        copied += 1
                        continue

                    # Download from S3 into memory and upload into GCS.
                    data = s3.get_object(Bucket=source_bucket, Key=key)['Body'].read()
                    from django.core.files.base import ContentFile
                    default_storage.save(key, ContentFile(data))
                    self.stdout.write(f"[ok] copied: {key}")
                    copied += 1
                except Exception as e:
                    failed += 1
                    logger.error(f"Failed to migrate key '{key}': {e}")

            else:
                continue
            break

        self.stdout.write(
            self.style.SUCCESS(
                f"Migration finished. Copied: {copied}, Skipped: {skipped}, Failed: {failed}. "
                "S3 objects were NOT deleted. Verify the data in GCS, then delete S3 manually."
            )
        )
