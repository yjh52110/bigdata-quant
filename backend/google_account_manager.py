import os
import json
import logging
from typing import List, Dict, Any, Optional
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Ensure data directory exists
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

ACCOUNTS_FILE = os.path.join(DATA_DIR, "google_accounts.json")
CREDENTIALS_FILE = os.path.join(DATA_DIR, "credentials.json")
ENCRYPTION_KEY_FILE = os.path.join(DATA_DIR, "encryption.key")

SCOPES = ["https://www.googleapis.com/auth/drive"]

class GoogleAccountManager:
    def __init__(self):
        self._init_encryption_key()
        self.accounts = self._load_accounts()

    def _init_encryption_key(self):
        if not os.path.exists(ENCRYPTION_KEY_FILE):
            key = Fernet.generate_key()
            with open(ENCRYPTION_KEY_FILE, "wb") as f:
                f.write(key)
        with open(ENCRYPTION_KEY_FILE, "rb") as f:
            self.fernet = Fernet(f.read())

    def _encrypt(self, data: str) -> str:
        return self.fernet.encrypt(data.encode()).decode()

    def _decrypt(self, data: str) -> str:
        return self.fernet.decrypt(data.encode()).decode()

    def _load_accounts(self) -> Dict[str, dict]:
        if not os.path.exists(ACCOUNTS_FILE):
            return {}
        with open(ACCOUNTS_FILE, "r") as f:
            return json.load(f)

    def _save_accounts(self):
        with open(ACCOUNTS_FILE, "w") as f:
            json.dump(self.accounts, f, indent=2)

    def get_oauth_flow(self, redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob") -> Flow:
        if not os.path.exists(CREDENTIALS_FILE):
            raise FileNotFoundError(f"Missing {CREDENTIALS_FILE}. Please provide OAuth client secrets.")
        return Flow.from_client_secrets_file(
            CREDENTIALS_FILE,
            scopes=SCOPES,
            redirect_uri=redirect_uri
        )

    def generate_auth_url(self, account_index: str, redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob") -> str:
        flow = self.get_oauth_flow(redirect_uri)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="select_account consent",
            state=account_index
        )
        return auth_url

    def handle_callback(self, account_index: str, code: str, redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob"):
        flow = self.get_oauth_flow(redirect_uri)
        flow.fetch_token(code=code)
        creds = flow.credentials
        
        service = build("drive", "v3", credentials=creds)
        about = service.about().get(fields="user").execute()
        email = about.get("user", {}).get("emailAddress", "unknown")

        self.accounts[account_index] = {
            "account_index": account_index,
            "email": email,
            "refresh_token": self._encrypt(creds.refresh_token) if creds.refresh_token else None,
            "is_connected": True
        }
        self._save_accounts()
        logging.info(f"Account {account_index} ({email}) successfully authenticated and saved.")

    def _build_service(self, account: dict):
        if not account.get("refresh_token"):
            raise ValueError("No refresh token available")
        
        refresh_token = self._decrypt(account["refresh_token"])
        with open(CREDENTIALS_FILE) as f:
            client_config = json.load(f)
        
        web = client_config.get("web") or client_config.get("installed")
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=web["client_id"],
            client_secret=web["client_secret"],
            scopes=SCOPES
        )
        return build("drive", "v3", credentials=creds)

    def get_storage_quota(self, account_index: str) -> dict:
        account = self.accounts.get(account_index)
        if not account or not account.get("is_connected"):
            return {"account_index": account_index, "is_connected": False, "free": 0}

        try:
            service = self._build_service(account)
            result = service.about().get(fields="storageQuota").execute()
            quota = result.get("storageQuota", {})
            used = int(quota.get("usage", 0))
            limit = int(quota.get("limit", 15 * 1024**3))
            free = max(0, limit - used)
            
            return {
                "account_index": account_index,
                "email": account.get("email"),
                "is_connected": True,
                "used": used,
                "limit": limit,
                "free": free,
                "health": "ok"
            }
        except RefreshError:
            account["is_connected"] = False
            self._save_accounts()
            return {"account_index": account_index, "is_connected": False, "free": 0, "health": "expired"}
        except Exception as e:
            logging.error(f"Error fetching quota for {account_index}: {e}")
            return {"account_index": account_index, "is_connected": account.get("is_connected"), "free": 0, "health": "error"}

    def get_all_quotas(self) -> List[dict]:
        results = []
        for index in self.accounts:
            results.append(self.get_storage_quota(index))
        return results

    def most_available(self) -> Optional[str]:
        quotas = self.get_all_quotas()
        connected = [q for q in quotas if q.get("is_connected") and q.get("free", 0) > 0]
        if not connected:
            return None
        best = max(connected, key=lambda q: q["free"])
        return best["account_index"]

    def generate_rclone_config(self, config_path: str):
        import configparser
        config = configparser.ConfigParser()
        if os.path.exists(config_path):
            config.read(config_path)

        for index, acc in self.accounts.items():
            if not acc.get("is_connected"):
                continue
            section = f"gdrive_{index}"
            if not config.has_section(section):
                config.add_section(section)
            
            config.set(section, "type", "drive")
            config.set(section, "scope", "drive")
            
            with open(CREDENTIALS_FILE) as f:
                client_config = json.load(f)
            web = client_config.get("web") or client_config.get("installed")
            config.set(section, "client_id", web["client_id"])
            config.set(section, "client_secret", web["client_secret"])
            
            if acc.get("refresh_token"):
                rclone_token = {
                    "access_token": "dummy",
                    "token_type": "Bearer",
                    "refresh_token": self._decrypt(acc["refresh_token"]),
                    "expiry": "2026-01-01T00:00:00Z"
                }
                config.set(section, "token", json.dumps(rclone_token))

        with open(config_path, "w") as f:
            config.write(f)
        logging.info(f"Generated rclone config at {config_path}")

    def get_account_pool_status(self) -> dict:
        quotas = self.get_all_quotas()
        total_accounts = len(self.accounts)
        active_accounts = len([q for q in quotas if q.get("is_connected")])
        exhausted_accounts = total_accounts - active_accounts
        
        total_capacity = sum(q.get("limit", 0) for q in quotas)
        used_capacity = sum(q.get("used", 0) for q in quotas)
        
        return {
            "total_accounts": total_accounts,
            "active_accounts": active_accounts,
            "exhausted_accounts": exhausted_accounts,
            "total_capacity_tb": total_capacity / (1024**4) if total_capacity else 0,
            "used_capacity_tb": used_capacity / (1024**4) if used_capacity else 0,
            "best_account_for_upload": self.most_available(),
            "health_status": "ok" if active_accounts > 0 else "degraded"
        }

if __name__ == "__main__":
    if not os.path.exists(CREDENTIALS_FILE):
        dummy_creds = {
            "installed": {
                "client_id": "dummy_client_id",
                "client_secret": "dummy_secret"
            }
        }
        with open(CREDENTIALS_FILE, "w") as f:
            json.dump(dummy_creds, f)

    manager = GoogleAccountManager()
    print("Google Account Manager Standalone Status:")
    print(json.dumps(manager.get_account_pool_status(), indent=2))
