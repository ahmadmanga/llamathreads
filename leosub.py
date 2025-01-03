import os
import logging
import requests
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from beem import Hive
from beem.account import Account
from beem.exceptions import MissingKeyError
from supabase import create_client

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants with default values
DEFAULT_API_NODE = 'https://api.hive.blog'
HIVE_API_NODES = os.getenv('HIVE_API_NODES', DEFAULT_API_NODE).split(',')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
CREATOR_SUB_ACC = os.getenv('CREATOR_SUB_ACC')
ACCOUNT = os.getenv('ACCOUNT')
ACTIVE_KEY = os.getenv('ACTIVE_KEY')
POSTING_KEY = os.getenv('POSTING_KEY')
MIN_HBD = float(os.getenv('MIN_HBD', 0.20))
MIN_HIVE = float(os.getenv('MIN_HIVE', 0.50))
MAX_HBD = 30 * MIN_HBD
MAX_HIVE = 30 * MIN_HIVE

# Initialize Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize Hive client with active key
hive = Hive(node=HIVE_API_NODES[0], keys=[ACTIVE_KEY])
account = Account(ACCOUNT, blockchain_instance=hive)

# Messages that can be easily edited
SUBSCRIPTION_ADD_MESSAGE = "Thank you @{} for subscribing to `llamathreads`. Your subscription starts at {} and ends at {}!"
SUBSCRIPTION_REMOVE_MESSAGE = "Your subscription to `llamathreads` has ended. Thanks for the conversations! Feel free to subscribe again. [Usage Instructions](https://inleo.io/threads/view/llamathreads/re-leothreads-2tychfjaq?referral=llamathreads)"

def is_valid_api_node(api_node):
    try:
        payload = {
            "jsonrpc": "2.0",
            "method": "condenser_api.get_account_count",
            "params": [],
            "id": 1
        }
        response = requests.post(api_node, json=payload, timeout=5)
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.warning(f'API node {api_node} is not responsive: {e}')
        return False

# Function to fetch account history
def fetch_account_history(account_name, start=-1, limit=1000):
    payload = {
        "jsonrpc": "2.0",
        "method": "condenser_api.get_account_history",
        "params": [account_name, start, limit],
        "id": 1
    }
    try:
        response = requests.post(HIVE_API_NODES[0], json=payload)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f'Error fetching account history from {HIVE_API_NODES[0]}: {e}')
        return None

# Function to process and filter transfers
def process_transfers(data, subscription_payment_account, creator_sub_acc):
    if data is None or 'result' not in data:
        logger.error('Unexpected API response format')
        return [], []
    valid_transfers = []
    invalid_transfers = []
    current_time = datetime.utcnow()
    thirty_one_days_ago = current_time - timedelta(days=31)
    for operation in data['result']:
        op_details = operation[1]
        if op_details['op'][0] == 'transfer':
            transfer = op_details['op'][1]
            transfer_time = datetime.strptime(op_details['timestamp'], '%Y-%m-%dT%H:%M:%S')
            if (transfer['to'] == subscription_payment_account and 
                (transfer['amount'].endswith(' HIVE') or transfer['amount'].endswith(' HBD')) and 
                transfer['memo'].lower() == f'subscribe:{creator_sub_acc}'):
                if transfer_time >= thirty_one_days_ago:
                    valid_transfers.append({
                        'username': transfer['from'],
                        'timestamp': transfer_time
                    })
                else:
                    invalid_transfers.append({
                        'username': transfer['from'],
                        'timestamp': transfer_time,
                        'days_off': (current_time - transfer_time).days
                    })
    return valid_transfers, invalid_transfers

# Function to update subscribers in Supabase
def update_subscribers(valid_transfers):
    current_time = datetime.utcnow()
    thirty_one_days_ago = current_time - timedelta(days=31)
    # Delete old subscribers
    supabase.table('subscribers').delete().lt('timestamp', thirty_one_days_ago.isoformat()).execute()
    # Update or insert new subscribers
    for transfer in valid_transfers:
        subscriber_data = {
            'username': transfer['username'],
            'timestamp': transfer['timestamp'].isoformat()
        }
        supabase.table('subscribers').upsert(subscriber_data).execute()

# Function to get the list of subscribers
def subscribers_list(subscription_payment_account, creator_sub_acc):
    global HIVE_API_NODES
    if not is_valid_api_node(HIVE_API_NODES[0]):
        logger.warning(f'Using default API node {DEFAULT_API_NODE} as {HIVE_API_NODES[0]} is not responsive.')
        HIVE_API_NODES = [DEFAULT_API_NODE]
    data = fetch_account_history(subscription_payment_account)
    valid_transfers, invalid_transfers = process_transfers(data, subscription_payment_account, creator_sub_acc)
    for invalid_transfer in invalid_transfers:
        logger.info(f"Invalid subscription for user {invalid_transfer['username']} - Off by {invalid_transfer['days_off']} days")
    update_subscribers(valid_transfers)
    subscribers = supabase.table('subscribers').select('*').execute().data
    free_trials = supabase.table('freetrial').select('*').execute().data
    all_users = [subscriber['username'] for subscriber in subscribers] + [free_trial['username'] for free_trial in free_trials]
    all_users = list(set(all_users))
    logger.info(f'Total users with valid subscriptions or free trial: {len(all_users)}')
    return all_users

# Function to add buyers
def add_buyers():
    current_time = datetime.utcnow()
    one_day_ago = current_time - timedelta(days=1)
    twenty_four_hours_ago = current_time - timedelta(hours=24)
    limit = 1000
    start = -1
    valid_buyers = []
    # Delete old processed transfers
    supabase.table('processed_transfers').delete().lt('timestamp', twenty_four_hours_ago.isoformat()).execute()
    while True:
        data = fetch_account_history(ACCOUNT, start, limit)
        if data is None or 'result' not in data:
            logger.error('Unexpected API response format')
            break
        for operation in data['result']:
            op_details = operation[1]
            if op_details['op'][0] == 'transfer':
                transfer = op_details['op'][1]
                transfer_time = datetime.strptime(op_details['timestamp'], '%Y-%m-%dT%H:%M:%S')
                if transfer_time < twenty_four_hours_ago:
                    # If the transfer is older than 24 hours, skip it
                    continue
                amount_value = float(transfer['amount'].split()[0])
                amount_currency = transfer['amount'].split()[1]
                tx_id = op_details['trx_id']
                if supabase.table('processed_transfers').select('*').eq('tx_id', tx_id).execute().data:
                    logger.info(f"Transfer {tx_id} already processed. Skipping.")
                    continue
                if transfer['to'] == ACCOUNT:
                    if amount_currency == 'HBD':
                        days = calculate_days(amount_value, MIN_HBD, MAX_HBD)
                    elif amount_currency == 'HIVE':
                        days = calculate_days(amount_value, MIN_HIVE, MAX_HIVE)
                    else:
                        logger.error(f"Unsupported currency: {amount_currency}")
                        continue
                    if days == 0:
                        send_transfer(transfer['from'], amount_value, amount_currency, f"Returning {transfer['amount']} as it is not within the acceptable threshold.")
                    else:
                        new_end_date = transfer_time + timedelta(days=days)
                        valid_buyers.append({
                            'username': transfer['from'],
                            'start_date': transfer_time.isoformat(),
                            'end_date': new_end_date.isoformat()
                        })
                        send_transfer(transfer['from'], 0.001, 'HIVE', f"Congrats! You're now subscribed for {days} day(s) to {ACCOUNT}'s services!")
                        notify_user_on_subscription_change(transfer['from'], transfer_time, new_end_date, True)
                        supabase.table('processed_transfers').insert({
                            'tx_id': tx_id,
                            'timestamp': transfer_time.isoformat()
                        }).execute()
        if data['result']:
            oldest_transaction_time = datetime.strptime(data['result'][0][1]['timestamp'], '%Y-%m-%dT%H:%M:%S')
            if oldest_transaction_time >= twenty_four_hours_ago:
                start = data['result'][0][0]
                continue
        break
    old_buyers = supabase.table('buyers').select('*').lt('end_date', one_day_ago.isoformat()).execute().data
    for old_buyer in old_buyers:
        send_transfer(old_buyer['username'], 0.001, 'HIVE', f"Your subscription to `{ACCOUNT}` has ended. Thanks for using it!")
        notify_user_on_subscription_change(old_buyer['username'], old_buyer['start_date'], old_buyer['end_date'], False)
    supabase.table('buyers').delete().lt('end_date', one_day_ago.isoformat()).execute()
    for buyer in valid_buyers:
        buyer_data = {
            'username': buyer['username'],
            'start_date': buyer['start_date'],
            'end_date': buyer['end_date']
        }
        supabase.table('buyers').upsert(buyer_data).execute()
    active_buyers = supabase.table('buyers').select('*').execute().data
    all_buyers = [buyer['username'] for buyer in active_buyers]
    logger.info(f'Total active buyers: {len(all_buyers)}')
    return all_buyers

# Function to calculate the number of days based on the amount transferred
def calculate_days(amount, min_amount, max_amount):
    if amount < min_amount:
        return 0
    elif amount > max_amount:
        return 30
    else:
        return int(amount / min_amount)

# Function to send a transfer
def send_transfer(to_account, amount, asset, memo):
    try:
        logger.info(f"Attempting to transfer {amount} {asset} to {to_account} with memo: {memo}")
        account.transfer(to_account, amount, asset, memo)
        logger.info(f"Transfer of {amount} {asset} to {to_account} successful with memo: {memo}")
        time.sleep(3)
    except Exception as e:
        logger.error(f"Error sending transfer of {amount} {asset} to {to_account} with memo: {memo}. Error: {e}")

# Function to get the latest author comment
def get_latest_author_comment(username):
    url = HIVE_API_NODES[0]
    payload = {
        "jsonrpc": "2.0",
        "method": "account_history_api.get_account_history",
        "params": {
            "account": username,
            "start": -1,
            "limit": 1,  # Adjust limit as needed
            "operation_filter_low": 2  # 2 means a comment operation
        },
        "id": 1
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        response_json = response.json()
        history = response_json.get('result', {}).get('history', [])
        # Reverse the history to find the latest comment where the user is the author
        for operation in reversed(history):
            op_details = operation[1]
            op_type = op_details.get('op', {}).get('type')
            if op_type == 'comment_operation':
                comment = op_details['op']['value']
                if comment['author'] == username:
                    logger.info(f"Found latest author comment by {username}: {comment['permlink']}")
                    return {
                        "author": comment['author'],
                        "permlink": comment['permlink']
                    }
        logger.info(f"No author comment found for user {username}.")
        return None
    except requests.RequestException as e:
        logger.error(f"Error fetching account history for user {username}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error processing response for user {username}: {e}")
        return None

# Function to notify user on subscription change
def notify_user_on_subscription_change(username, start_date, end_date, is_addition):
    try:
        # Send transaction notification
        send_transfer(username, 0.001, 'HIVE', f"Notification: Your subscription {'started' if is_addition else 'ended'}.")
        # Fetch the latest author comment
        parent_comment = get_latest_author_comment(username)
        if parent_comment:
            reply_text = ""
            if is_addition:
                reply_text = SUBSCRIPTION_ADD_MESSAGE.format(username, start_date.strftime('%Y-%m-%d %H:%M:%S'), end_date.strftime('%Y-%m-%d %H:%M:%S'))
            else:
                reply_text = SUBSCRIPTION_REMOVE_MESSAGE
            # Replace "@llamathreads" with "`llamathreads`" to prevent tagging
            reply_text = reply_text.replace('@llamathreads', '`llamathreads`')
            # Generate a unique permlink for your comment and convert it to lowercase
            permlink = f"re-{parent_comment['author']}-{parent_comment['permlink']}-{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}"
            permlink = permlink.lower()
            hive = Hive(node=HIVE_API_NODES[0], keys=[POSTING_KEY])
            result = hive.post(
                title="",  # Leave empty for a comment
                body=reply_text,
                author=ACCOUNT,
                permlink=permlink,
                reply_identifier=f"{parent_comment['author']}/{parent_comment['permlink']}",
                json_metadata={"app": "leothreads/0.3"}  # Use Leothreads interface for posting to the blockchain
            )
            logger.info(f"Notification sent to {username} regarding subscription change with reply text: {reply_text}")
        else:
            logger.info(f"No author comment found for {username}. No comment notification sent.")
    except MissingKeyError:
        logger.error("Missing posting key. Please check your POSTING_KEY in the .env file.")
    except Exception as e:
        logger.error(f"An error occurred while notifying user {username} of subscription change: {e}")
        logger.debug(str(e))

# Function to switch Hive API node
def switch_hive_node():
    global HIVE_API_NODES
    current_index = HIVE_API_NODES.index(HIVE_API_NODES[0])
    next_index = (current_index + 1) % len(HIVE_API_NODES)
    HIVE_API_NODES[0] = HIVE_API_NODES[next_index]
    hive.set_nodes([HIVE_API_NODES[0]])
    logger.info(f"Switched to new API node: {HIVE_API_NODES[0]}")

# Example usage
def list_all_users():
    SUBSCRIPTION_PAYMENT_ACCOUNT = 'leosubscriptions'
    subscribers = subscribers_list(SUBSCRIPTION_PAYMENT_ACCOUNT, CREATOR_SUB_ACC)
    buyers = add_buyers()
    all_users = list(set(subscribers + buyers))
    print(all_users)
    return all_users
