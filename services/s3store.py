from __future__ import annotations
import os, io, mimetypes, time
from typing import Optional
import boto3
from botocore.exceptions import BotoCoreError, ClientError

def s3_enabled() -> bool:
    return bool(os.getenv("S3_BUCKET"))

def _client():
    return boto3.client(
        "s3",
        region_name=os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN"),
    )

def upload_bytes(key: str, data: bytes, content_type: Optional[str] = None) -> Optional[str]:
    if not s3_enabled():
        return None
    ct = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
    try:
        _client().put_object(Bucket=os.getenv("S3_BUCKET"), Key=key, Body=data, ContentType=ct)
        return key
    except (BotoCoreError, ClientError) as e:
        print("S3 upload error:", e)
        return None

def presigned_url(key: str, expires: int = 3600) -> Optional[str]:
    if not s3_enabled():
        return None
    try:
        return _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": os.getenv("S3_BUCKET"), "Key": key},
            ExpiresIn=expires
        )
    except (BotoCoreError, ClientError) as e:
        print("S3 presign error:", e)
        return None
