import os
from pathlib import Path
from dotenv import load_dotenv
BASE=Path(__file__).resolve().parent.parent
load_dotenv(BASE/'.env')
DATA=BASE/'data'
INBOX=DATA/'inbox'; PROCESSING=DATA/'processing'; OUTPUT=DATA/'output'; FAILED=DATA/'failed'; DOWNLOADS=DATA/'downloads'
for p in [INBOX,PROCESSING,OUTPUT,FAILED,DOWNLOADS]: p.mkdir(parents=True,exist_ok=True)
DB_PATH=DATA/'clipforge.sqlite3'
HOST=os.getenv('HOST','127.0.0.1'); PORT=int(os.getenv('PORT','8000'))
FFMPEG_BIN=os.getenv('FFMPEG_BIN','ffmpeg')
WHISPER_MODEL=os.getenv('WHISPER_MODEL','base'); WHISPER_DEVICE=os.getenv('WHISPER_DEVICE','cpu'); WHISPER_COMPUTE_TYPE=os.getenv('WHISPER_COMPUTE_TYPE','int8')
MAX_CLIPS_PER_SOURCE=int(os.getenv('MAX_CLIPS_PER_SOURCE','5')); MIN_CLIP_SECONDS=int(os.getenv('MIN_CLIP_SECONDS','15')); MAX_CLIP_SECONDS=int(os.getenv('MAX_CLIP_SECONDS','60'))
TIMEZONE=os.getenv('TIMEZONE','Asia/Kolkata')
DAILY_SLOTS=[x.strip() for x in os.getenv('DAILY_SLOTS','10:00,14:00,19:30').split(',')]
SCHEDULER_INTERVAL_SECONDS=int(os.getenv('SCHEDULER_INTERVAL_SECONDS','15'))
META_ACCESS_TOKEN=os.getenv('META_ACCESS_TOKEN','')
INSTAGRAM_BUSINESS_ACCOUNT_ID=os.getenv('INSTAGRAM_BUSINESS_ACCOUNT_ID','')
META_PAGE_ID=os.getenv('META_PAGE_ID','')
CLIPFORGE_S3_BUCKET=os.getenv('CLIPFORGE_S3_BUCKET','clipforge-temp-343218199395')
AWS_REGION=os.getenv('AWS_REGION','us-east-1')