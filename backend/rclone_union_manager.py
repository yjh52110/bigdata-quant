import os
import logging
import configparser
from backend.google_account_manager import GoogleAccountManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

RCLONE_CONFIG_PATH = os.path.expanduser("~/.config/rclone/rclone.conf")
UNION_REMOTE_NAME = "gdrive_union"
QUOTA_THRESHOLD = 0.95  # 95% full

def update_union_config():
    """Updates the rclone union remote configuration based on quota health from GoogleAccountManager."""
    manager = GoogleAccountManager()
    quotas = manager.get_all_quotas()
    
    if not quotas:
        logging.warning("No Google Drive accounts found.")
        return

    healthy_accounts = []
    
    for q in quotas:
        if not q.get("is_connected"):
            continue
        
        limit = q.get("limit", 1)
        used = q.get("used", 0)
        usage = used / limit if limit > 0 else 1.0
        
        acc_name = f"gdrive_{q['account_index']}"
        logging.info(f"Account {acc_name} usage: {usage * 100:.2f}%")
        
        if usage < QUOTA_THRESHOLD:
            healthy_accounts.append(acc_name)
            
    if not healthy_accounts:
        logging.error("No healthy accounts available. All drives are full or inaccessible.")
        return

    # Ensure config directory exists
    os.makedirs(os.path.dirname(RCLONE_CONFIG_PATH), exist_ok=True)
    
    # Generate individual drive remotes
    manager.generate_rclone_config(RCLONE_CONFIG_PATH)

    # Update union remote
    config = configparser.ConfigParser()
    config.read(RCLONE_CONFIG_PATH)
    
    if not config.has_section(UNION_REMOTE_NAME):
        config.add_section(UNION_REMOTE_NAME)
        
    config.set(UNION_REMOTE_NAME, "type", "union")
    upstreams = " ".join([f"{acc}:/" for acc in healthy_accounts])
    config.set(UNION_REMOTE_NAME, "upstreams", upstreams)
    # epmfs = "existing path, most free space": new files land on whichever healthy
    # account currently has the most free quota. This is what the PDF calls "配额
    # 超额自动流转调度" -- previously this key was never set, so the union silently
    # fell back to rclone's default policy instead of the promised routing behavior.
    config.set(UNION_REMOTE_NAME, "create_policy", "epmfs")
    config.set(UNION_REMOTE_NAME, "action_policy", "epall")
    config.set(UNION_REMOTE_NAME, "search_policy", "ff")
    
    with open(RCLONE_CONFIG_PATH, "w") as f:
        config.write(f)
        
    logging.info(f"Updated union remote '{UNION_REMOTE_NAME}' with {len(healthy_accounts)} healthy accounts.")

if __name__ == "__main__":
    logging.info("Starting rclone union quota health check and configuration update...")
    update_union_config()
    logging.info("Completed rclone union manager execution.")
