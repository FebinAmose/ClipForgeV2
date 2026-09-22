import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

import time
import boto3
import requests


def status():
    return {
        'youtube': Path(
            os.getenv('YOUTUBE_CLIENT_SECRET_FILE', 'client_secret.json')
        ).exists(),

        'instagram': bool(
            os.getenv('META_ACCESS_TOKEN')
            and os.getenv('INSTAGRAM_BUSINESS_ACCOUNT_ID')
        ),

        'facebook': bool(
            os.getenv('META_PAGE_ACCESS_TOKEN')
            and os.getenv('META_PAGE_ID')
        )
    }


def youtube_upload(path, title, description):
    secret = os.getenv(
        'YOUTUBE_CLIENT_SECRET_FILE',
        'client_secret.json'
    )
    token = os.getenv(
        'YOUTUBE_TOKEN_FILE',
        'data/youtube_token.json'
    )

    if not Path(secret).exists():
        raise RuntimeError(
            'YouTube OAuth client_secret.json is missing.'
        )

    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    scopes = [
        'https://www.googleapis.com/auth/youtube.upload'
    ]

    creds = None

    if Path(token).exists():
        creds = Credentials.from_authorized_user_file(
            token,
            scopes
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = InstalledAppFlow.from_client_secrets_file(
                secret,
                scopes
            ).run_local_server(port=0)

        Path(token).write_text(
            creds.to_json(),
            encoding='utf-8'
        )

    svc = build(
        'youtube',
        'v3',
        credentials=creds
    )

    body = {
        'snippet': {
            'title': title[:100],
            'description': description[:5000],
            'categoryId': '22'
        },
        'status': {
            'privacyStatus': 'public',
            'selfDeclaredMadeForKids': False
        }
    }

    req = svc.videos().insert(
        part='snippet,status',
        body=body,
        media_body=MediaFileUpload(
            path,
            chunksize=-1,
            resumable=True
        )
    )

    response = None

    while response is None:
        _, response = req.next_chunk()

    return response.get('id')


def meta_upload(path, title, description, platform):

    # =========================================================
    # FACEBOOK PAGE
    # =========================================================
    if platform == 'facebook':

        access_token = os.getenv(
            'META_PAGE_ACCESS_TOKEN'
        )

        if not access_token:
            raise RuntimeError(
                'META_PAGE_ACCESS_TOKEN is missing.'
            )

        page_id = os.getenv(
            'META_PAGE_ID'
        )

        if not page_id:
            raise RuntimeError(
                'META_PAGE_ID is missing.'
            )

        # Facebook Page video upload
        upload_url = (
            f'https://graph.facebook.com/v26.0/'
            f'{page_id}/videos'
        )

        with open(path, 'rb') as video_file:

            response = requests.post(
                upload_url,
                params={
                    'access_token': access_token,
                    'title': title[:255],
                    'description': description[:5000],
                },
                files={
                    'source': (
                        Path(path).name,
                        video_file,
                        'video/mp4'
                    )
                },
                timeout=300
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f'Facebook video upload failed: '
                f'{response.status_code} '
                f'{response.text}'
            )

        video_id = response.json().get('id')

        if not video_id:
            raise RuntimeError(
                f'Facebook did not return a video ID: '
                f'{response.text}'
            )

        return video_id


    # =========================================================
    # INSTAGRAM
    # =========================================================
    if platform == 'instagram':

        access_token = os.getenv(
            'META_ACCESS_TOKEN'
        )

        instagram_id = os.getenv(
            'INSTAGRAM_BUSINESS_ACCOUNT_ID'
        )

        if not access_token:
            raise RuntimeError(
                'META_ACCESS_TOKEN is missing.'
            )

        if not instagram_id:
            raise RuntimeError(
                'INSTAGRAM_BUSINESS_ACCOUNT_ID is missing.'
            )

        bucket = os.getenv(
            'CLIPFORGE_S3_BUCKET',
            'clipforge-temp-343218199395'
        )

        region = os.getenv(
            'AWS_REGION',
            'us-east-1'
        )

        # Upload clip to temporary S3 storage
        s3 = boto3.client(
            's3',
            region_name=region
        )

        object_key = (
            f'clipforge/{int(time.time())}/'
            f'{Path(path).name}'
        )

        s3.upload_file(
            path,
            bucket,
            object_key,
            ExtraArgs={
                'ContentType': 'video/mp4'
            }
        )

        # Generate temporary HTTPS URL for Instagram
        video_url = s3.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket,
                'Key': object_key
            },
            ExpiresIn=3600
        )

        graph_url = (
            'https://graph.instagram.com/'
            f'{instagram_id}/media'
        )

        # Create Instagram Reel container
        response = requests.post(
            graph_url,
            params={
                'media_type': 'REELS',
                'video_url': video_url,
                'caption': (
                    f'{title}\n\n'
                    f'{description}'
                )[:2200],
                'access_token': access_token
            },
            timeout=60
        )

        if response.status_code >= 400:
            raise RuntimeError(
                f'Instagram container creation failed: '
                f'{response.status_code} '
                f'{response.text}'
            )

        container_id = response.json().get('id')

        if not container_id:
            raise RuntimeError(
                f'Instagram did not return a container ID: '
                f'{response.text}'
            )

        # Wait for Instagram to download/process the video
        status_url = (
            f'https://graph.instagram.com/'
            f'{container_id}'
        )

        deadline = time.time() + 300

        while time.time() < deadline:

            status_response = requests.get(
                status_url,
                params={
                    'fields': 'status_code,status',
                    'access_token': access_token
                },
                timeout=30
            )

            if status_response.status_code >= 400:
                raise RuntimeError(
                    f'Instagram status check failed: '
                    f'{status_response.status_code} '
                    f'{status_response.text}'
                )

            data = status_response.json()
            status_code = data.get('status_code')

            if status_code == 'FINISHED':
                break

            if status_code == 'ERROR':
                raise RuntimeError(
                    f'Instagram media processing failed: '
                    f'{data.get("status", data)}'
                )

            time.sleep(10)

        else:
            raise RuntimeError(
                'Instagram media processing timed out after 5 minutes.'
            )

        # Publish the Reel
        publish_url = (
            'https://graph.instagram.com/'
            f'{instagram_id}/media_publish'
        )

        publish_response = requests.post(
            publish_url,
            params={
                'creation_id': container_id,
                'access_token': access_token
            },
            timeout=60
        )

        if publish_response.status_code >= 400:
            raise RuntimeError(
                f'Instagram publishing failed: '
                f'{publish_response.status_code} '
                f'{publish_response.text}'
            )

        media_id = publish_response.json().get('id')

        if not media_id:
            raise RuntimeError(
                f'Instagram did not return a media ID: '
                f'{publish_response.text}'
            )

        return media_id


    # =========================================================
    # UNSUPPORTED PLATFORM
    # =========================================================
    raise RuntimeError(
        f'{platform} publishing is not implemented yet.'
    )

