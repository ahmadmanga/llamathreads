import os
import sys
import threading
from listener import get_latest_block_num, get_block_range, load_last_block, save_last_block, listen_for_comments
from reply import talk_to_gpt, post_reply, fetch_comment_chain
from leosub import list_all_users  # Import the list_all_users function
from container_thread import container_thread_creator  # Added import for container_thread_creator
from datetime import datetime
import logging  # Configure logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger()

# Configuration
BLOCK_RANGE = 50
QUIT_TIMEOUT = 30  # 30 seconds timeout for quitting on error

# Instructional message for non-subscribers
INSTRUCTIONAL_MESSAGE = """It appears that you're not subscribed to **Llamathreads.** Please Subscribe and Try again.
* [Usage Instructions.](https://inleo.io/threads/view/llamathreads/re-leothreads-2tychfjaq?referral=llamathreads)
* Tag @ `ahmadmanga` for reporting issues."""

def main():
    # Load the last processed block number or get the latest block number if not available
    latest_block_num = get_latest_block_num()
    last_block = load_last_block() or latest_block_num
    end_block = last_block + BLOCK_RANGE - 1
    if latest_block_num < end_block:
        end_block = latest_block_num
    logger.info(f"Initial last_block: {last_block}, end_block: {end_block}, latest_block_num: {latest_block_num}")

    # Get the list of all users (subscribers and buyers)
    all_users = list_all_users()
    logger.info("Subscribers list generated.")

    # Call the container_thread_creator function with error handling
    try:
        logger.info("Starting container_thread_creator...")
        start_time = datetime.now()
        container_thread_creator()
        end_time = datetime.now()
        logger.info(f"Container thread creation attempted. Duration: {end_time - start_time}")
    except Exception as e:
        logger.error(f"Error in container_thread_creator: {e}")

    # Start listening for comments
    while True:
        try:
            # Check if the last block is already the latest block
            if last_block > latest_block_num:
                logger.info("Last block is greater than the latest block. Exiting the application.")
                save_last_block(last_block)
                break

            # Fetch comments within the valid block range
            comments = listen_for_comments(last_block, end_block)
            for comment in comments:
                # Ensure the comment body is encoded in UTF-8
                comment_body = comment['body'].encode('utf-8', errors='replace').decode('utf-8')
                print(f"Fetched comment by @{comment['author']} on {comment['block_timestamp']}: {comment_body}")

                # Check if the commenter is a subscriber
                if comment['author'] in all_users:
                    # Fetch the comment chain messages
                    messages = fetch_comment_chain(comment)

                    # Generate a response using the AI
                    system_prompt = None
                    prompt = comment_body
                    response = talk_to_gpt(prompt, system_prompt=None, messages=messages)

                    if response:
                        reply_text = response  # Directly use the response text
                        # Post the reply to the Hive blockchain
                        post_reply(comment, reply_text)
                    else:
                        # Post the instructional message if the user is not a subscriber
                        post_reply(comment, INSTRUCTIONAL_MESSAGE)
                else:
                    post_reply(comment, INSTRUCTIONAL_MESSAGE)

            # Update the block range for the next iteration
            last_block = end_block + 1
            end_block = last_block + BLOCK_RANGE - 1
            latest_block_num = get_latest_block_num()
            if latest_block_num < end_block:
                end_block = latest_block_num

            # Save last_block before exiting if reached the latest block
            save_last_block(last_block)
            logger.info(f"Updated last_block: {last_block}, end_block: {end_block}, latest_block_num: {latest_block_num}")
            if last_block == latest_block_num:
                print("Last block is the same as the latest block. Exiting the application.")
                save_last_block(last_block)
                break

            # Quit the loop to exit
        except Exception as e:
            print(f"An error occurred: {e}")
            quit_if_timeout()

def quit_if_timeout():
    """Wait for user input or timeout to quit the application."""
    print(f"No input received. The application will quit in {QUIT_TIMEOUT} seconds...")
    timeout_event = threading.Event()
    input_thread = threading.Thread(target=lambda: input("Press Enter to stop the application...") or timeout_event.set())
    input_thread.start()
    timeout_event.wait(QUIT_TIMEOUT)
    if not timeout_event.is_set():
        print("Timeout reached. Exiting the application.")
        os._exit(1)

if __name__ == "__main__":
    main()
