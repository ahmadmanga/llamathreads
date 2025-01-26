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
    try:
        hive = Hive(node='https://api.hive.blog')
        account = Account(author, hive_instance=hive)
        latest_post = None
        for post in account.get_blog(limit=1):  # Limit to 1 to get the latest post
            latest_post = post
            logger.info(f"Retrieved post: {post}")
            break
        if latest_post is None:
            logger.info(f"No posts found for account: {author}")
        return latest_post
    except MissingKeyError:
        logger.error("Missing posting key. Please check your environment variables.")
    except Exception as e:
        logger.error(f"An error occurred while fetching the latest post: {e}")
        logger.debug(str(e))
    return None

def make_naive(dt):
    """Convert an offset-aware datetime to offset-naive."""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt

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
                "isPoll": "false",
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

def get_last_container_thread_post_time():
    try:
        response = supabase.table('llamathreads_data').select('*').eq('_id', 'last_container_thread_check').execute()
        data = response.data
        if data and len(data) > 0 and 'value' in data[0]:
            last_post = data[0]['value']
            try:
                print(f"returning {last_post}")
                return datetime.fromisoformat(last_post)
            except ValueError:
                logger.warning("Stored last post value is not a valid datetime. Treating as if no check has been made.")
                return None
        else:
            logger.info("No previous post time found.")
            return None
    except Exception as e:
        logger.error(f"Error fetching last post time from Supabase: {e}")
        logger.debug(str(e))
        return None

def update_last_container_thread_post_time():
    current_time = datetime.utcnow().isoformat()
    try:
        response = supabase.table('llamathreads_data').upsert({'_id': 'last_container_thread_check', 'value': current_time}).execute()
        if response.status_code == 201 or response.status_code == 200:
            logger.info("Last post time updated successfully.")
        else:
            logger.error(f"Error updating last post time in Supabase: {response.status_code} - {response.message}")
    except Exception as e:
        logger.error(f"Exception updating last post time in Supabase: {str(e)}")
        logger.debug(str(e))

def container_thread_creator():
    logger.info("Starting Hive Container Thread application...")
    
    # Get last post time from Supabase
    last_post_time = get_last_container_thread_post_time()
    current_time = datetime.utcnow()
    
    # If we can't access Supabase, skip the process
    if last_post_time is None:
        logger.error("Could not access Supabase. Skipping container thread creation.")
        return
    else:
       print(f"last_post_time is not None: {last_post_time}")

    # Only post if it's been more than 24 hours since the last post
    current_time = make_naive(current_time)
    last_post_time = make_naive(last_post_time)
    time_difference = current_time - last_post_time
    is_post_recent = time_difference < timedelta(hours=24)
    if is_post_recent:
        logger.info("Container thread posted too recently. Skipping.")
        return
    else:
       print(f"Another Step Done: {time_difference}")

    # Get the latest post by leothreads
    latest_post = get_latest_post('leothreads')
    if not latest_post:
        logger.error("Failed to fetch the latest post by leothreads.")
        return
    
    logger.info(f"Latest post by leothreads: {latest_post.permlink}")
    
    # Post a new container thread
    logger.info("Posting new container thread...")
    post_container_thread(latest_post, CONTAINER_THREAD)
    
    # Update the last post time in Supabase
    update_last_container_thread_post_time()
    
    logger.info("Application completed.")

if __name__ == "__main__":
    container_thread_creator()