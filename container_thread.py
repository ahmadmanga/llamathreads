import os
import sys
from datetime import datetime, timedelta
from beem import Hive
from beem.account import Account
from beem.comment import Comment
from beem.exceptions import MissingKeyError
from beemapi.exceptions import InvalidParameters
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger()

# Load environment variables
load_dotenv()

# Get environment variables
ACCOUNT = os.getenv('ACCOUNT')
POSTING_KEY = os.getenv('POSTING_KEY')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# Default MAIN_TAG and CONTAINER_THREAD provided directly in code
MAIN_TAGS = ["#threadcast"]
CONTAINER_THREAD = """## LlamaThreads #threadcast!

I am **LlamaThreads.** A Chatbot based on Meta's open-source Model Llama 3.3. Tag me or reply below to prompt me for anything. Feel free to prompt me for summarization, translation or explainations. Anything to do with text generation. I will do my best to answer your needs. 


For more info, read: https://inleo.io/threads/view/llamathreads/re-leothreads-2tychfjaq?referral=llamathreads


https://img.inleo.io/DQmeVDFM7F3F6jmRhWpwsFYGHuPPrTjpttPUBX1xMujMyMC/VeniceAI_0hBbOYe_Square.jpg"""

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_latest_post(author):
    hive = Hive(node='https://api.hive.blog')
    account = Account(author, hive_instance=hive)
    latest_post = None
    for post in account.get_blog():
        latest_post = post
        break
    return latest_post

def make_naive(dt):
    """Convert an offset-aware datetime to offset-naive."""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt

def check_container_thread_exists(account, tags, start_time):
    start_time = make_naive(start_time)  # Ensure start_time is offset-naive
    comments_checked = 0
    comments_skipped = 0
    error_count = 0
    limit = 100
    last_permlink = None
    c_list = {}
    while True:
        logger.info(f"Starting history search with limit {limit} from permlink {last_permlink}...")
        if last_permlink:
            history = list(account.comment_history(limit=limit, start_permlink=last_permlink))
        else:
            history = list(account.comment_history(limit=limit))
        if not history:
            logger.info("No more comments in history.")
            break
        # Ensure 'created' is a datetime object
        sorted_history = sorted(history, key=lambda x: make_naive(x['created']), reverse=True)
        for op in sorted_history:
            comments_checked += 1
            authorperm = f"@{op['author']}/{op['permlink']}"
            permalink = op['permlink']
            if permalink in c_list:
                comments_skipped += 1
                log_message = f"Checked: {comments_checked}, Skipped: {comments_skipped}, Error: {error_count}, Current: {authorperm}"
                sys.stdout.write(f"\r{log_message}")
                sys.stdout.flush()
                continue
            try:
                comment = Comment(authorperm)
                comment.refresh()
            except InvalidParameters as e:
                comments_skipped += 1
                log_message = f"Checked: {comments_checked}, Skipped: {comments_skipped}, Error: {error_count}, Current: {authorperm}"
                sys.stdout.write(f"\r{log_message}")
                sys.stdout.flush()
                logger.warning(f"Invalid Parameters for {authorperm}. Skipping.")
                logger.debug(str(e))
                continue
            except Exception as e:
                if 'content does not exist' in str(e).lower() or 'invalid parameters' in str(e).lower():
                    comments_skipped += 1
                    log_message = f"Checked: {comments_checked}, Skipped: {comments_skipped}, Error: {error_count}, Current: {authorperm}"
                    sys.stdout.write(f"\r{log_message}")
                    sys.stdout.flush()
                    logger.warning(f"Content does not exist for {authorperm}. Skipping.")
                    logger.debug(str(e))
                    continue
                error_count += 1
                log_message = f"Checked: {comments_checked}, Skipped: {comments_skipped}, Error: {error_count}, Current: {authorperm}"
                sys.stdout.write(f"\r{log_message}")
                sys.stdout.flush()
                logger.error(f"Error for {authorperm}.")
                logger.debug(str(e))
                continue
            c_list[comment.permlink] = 1
            if comment.is_comment():
                body = comment.body
                timestamp = make_naive(comment['created'])  # Ensure timestamp is offset-naive
                parent_author = comment.parent_author
                if timestamp < start_time:
                    logger.info("Reached comments older than 24 hours. Stopping.")
                    logger.info(f"Checked: {comments_checked}, Skipped: {comments_skipped}, Error: {error_count}, Current: {authorperm}")
                    return False
                if timestamp >= start_time:
                    if parent_author == 'leothreads':
                        for tag in tags:
                            if tag in body:
                                logger.info("Found container thread.")
                                logger.info(f"Checked: {comments_checked}, Skipped: {comments_skipped}, Error: {error_count}, Current: {authorperm}")
                                return True
                log_message = f"Checked: {comments_checked}, Skipped: {comments_skipped}, Error: {error_count}, Current: {authorperm}"
                sys.stdout.write(f"\r{log_message}")
                sys.stdout.flush()
        # Update last_permlink to the last entry in the current batch
        last_permlink = sorted_history[-1]['permlink']
        # Adjust limit for the next iteration
        limit *= 3
    logger.info("Complete. No container thread found.")
    logger.info(f"Checked: {comments_checked}, Skipped: {comments_skipped}, Error: {error_count}")
    return False

def post_container_thread(parent_post, container_thread_text):
    hive = Hive(node='https://api.hive.blog', keys=[POSTING_KEY])
    try:
        # Generate a unique permlink for your comment and convert it to lowercase
        permlink = f"re-{parent_post.author}-{parent_post.permlink}-{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}"
        permlink = permlink.lower()
        result = hive.post(
            title="",  # Leave empty for a comment
            body=container_thread_text,
            author=ACCOUNT,
            permlink=permlink,
            reply_identifier=f"{parent_post.author}/{parent_post.permlink}",
            json_metadata={
                "app": "leothreads/0.3",
                "canonical_url": "https://inleo.io/threads/view/{permlink}",
                "dimensions": {},
                "format": "markdown",
                "images": [],
                "isPoll": false,
                "links": [],
                "pollOptions": {},
                "tags": ["leofinance"]
                }  # Change this to have the same meta-patterns of other threadcasts "leothreads/0.3"
                )
        logger.info(f"Container thread posted successfully: {result}")
    except MissingKeyError:
        logger.error("Missing posting key. Please check your POSTING_KEY in the .env file.")
    except Exception as e:
        logger.error(f"An error occurred while posting the container thread: {e}")
        logger.debug(str(e))

def get_last_container_thread_check():
    try:
        response = supabase.table('llamathreads_data').select('*').eq('_id', 'last_container_thread_check').execute()
        data = response.data
        if data and len(data) > 0 and 'value' in data[0]:
            last_check = data[0]['value']
            try:
                return datetime.fromisoformat(last_check)
            except ValueError:
                logger.warning("Stored last check value is not a valid datetime. Treating as if no check has been done.")
                return None
        else:
            logger.info("No previous check found.")
            return None
    except Exception as e:
        logger.error(f"Error fetching last check time from Supabase: {e}")
        logger.debug(str(e))
        return None

def update_last_container_thread_check():
    current_time = datetime.utcnow().isoformat()
    try:
        response = supabase.table('llamathreads_data').upsert({'_id': 'last_container_thread_check', 'value': current_time}).execute()
        if response.error:
            logger.error(f"Error updating last check time in Supabase: {response.error}")
        else:
            logger.info("Last check time updated successfully.")
    except Exception as e:
        logger.error(f"Error updating last check time in Supabase: {e}")
        logger.debug(str(e))

def container_thread_creator():
    logger.info("Starting Hive Container Thread application...")
    # Check last container thread check time
    last_check = get_last_container_thread_check()
    current_time = datetime.utcnow()
    if last_check and current_time - last_check < timedelta(hours=2):
        logger.info("Container thread check performed in the last 2 hours. Skipping check.")
        return

    # Get the latest post by leothreads
    latest_post = get_latest_post('leothreads')
    if not latest_post:
        logger.error("Failed to fetch the latest post by leothreads.")
        return
    logger.info(f"Latest post by leothreads: {latest_post.permlink}")
    # Check if a container thread already exists in the last 24 hours
    account = Account(ACCOUNT, hive_instance=Hive(node='https://api.hive.blog'))
    start_time = datetime.now() - timedelta(days=1)
    if check_container_thread_exists(account, MAIN_TAGS, start_time):
        logger.info("Container thread already exists.")
    else:
        logger.info("No container thread found. Creating a new one...")
        post_container_thread(latest_post, CONTAINER_THREAD)
    logger.info("Application completed.")
    # Update the last container thread check time
    update_last_container_thread_check()

if __name__ == "__main__":
    container_thread_creator()
