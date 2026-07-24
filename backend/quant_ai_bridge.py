import os
import time
import logging
from typing import List, Dict, Any, Optional
import duckdb
from google import genai

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class MultiKeyGeminiPool:
    """
    Manages a pool of Google AI Studio Gemini API keys.
    Features:
    - Round-robin key rotation to stay within 1,500 RPD free tier limits per key
    - Exponential backoff upon encountering 429 Rate Limit errors
    - Cooldown management for exhausted keys
    """
    def __init__(self, api_keys: List[str]):
        if not api_keys:
            env_keys = os.environ.get("GEMINI_API_KEYS")
            single_key = os.environ.get("GEMINI_API_KEY")
            if env_keys:
                api_keys = [k.strip() for k in env_keys.split(",") if k.strip()]
            elif single_key:
                api_keys = [single_key]
            else:
                api_keys = ["AIzaSyMockKeyForDevTestOnly12345"]
        self.api_keys = api_keys
        self.current_index = 0
        self.key_cooldowns: Dict[str, float] = {key: 0.0 for key in api_keys}

    def _get_next_available_key(self) -> str:
        now = time.time()
        start_idx = self.current_index
        
        while True:
            key = self.api_keys[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.api_keys)
            
            # Check if key is out of cooldown
            if now >= self.key_cooldowns[key]:
                return key
                
            # If all keys are in cooldown, pick the one with shortest cooldown and wait
            if self.current_index == start_idx:
                shortest_wait = min(self.key_cooldowns.values()) - now
                if shortest_wait > 0:
                    logging.warning(f"All Gemini API keys in cooldown. Waiting {shortest_wait:.2f} seconds...")
                    time.sleep(shortest_wait)
                    now = time.time()
                return self.api_keys[0]

    def _mark_key_cooldown(self, key: str, duration_seconds: float = 60.0):
        self.key_cooldowns[key] = time.time() + duration_seconds
        logging.warning(f"Gemini API key {key[:8]}... placed on cooldown for {duration_seconds} seconds.")

    def generate_content_with_retry(self, prompt: str, model_name: str = "gemini-2.5-flash", max_retries: int = 3) -> Optional[str]:
        """
        Attempts to generate content, rotating keys and using exponential backoff on 429/failures.
        """
        for attempt in range(max_retries):
            key = self._get_next_available_key()
            try:
                logging.info(f"Using Gemini API key ending in ...{key[-4:]} (Attempt {attempt+1}/{max_retries})")
                client = genai.Client(api_key=key)
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                return response.text
            except Exception as e:
                err_str = str(e)
                logging.error(f"Error during Gemini API call: {err_str}")
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    # Rate limit hit, apply exponential backoff cooldown
                    cooldown = (2 ** attempt) * 30
                    self._mark_key_cooldown(key, duration_seconds=cooldown)
                else:
                    # Other error, short cooldown
                    self._mark_key_cooldown(key, duration_seconds=10)
                
                time.sleep(1)
                
        logging.error("Exhausted max retries for Gemini API generation.")
        return None

    def analyze_hypersync_data(self, parquet_glob_pattern: str, user_prompt: str) -> Optional[str]:
        """
        Queries Parquet files ingested via Hypersync using DuckDB, 
        summarizes results, and passes them to Gemini for insights.
        """
        logging.info(f"Analyzing Hypersync Parquet data at {parquet_glob_pattern}...")
        try:
            con = duckdb.connect(':memory:')
            # Sample query to extract recent records
            df = con.execute(f"SELECT * FROM read_parquet('{parquet_glob_pattern}') LIMIT 20").df()
            data_json = df.to_json(orient='records')
            
            combined_prompt = (
                f"You are an expert blockchain quant AI. Analyze the following Hypersync-ingested data snippet:\n"
                f"Data Snippet:\n{data_json}\n\n"
                f"User Request: {user_prompt}\n\n"
                f"Provide actionable quant insights and risk assessment."
            )
            return self.generate_content_with_retry(combined_prompt)
        except Exception as e:
            logging.error(f"DuckDB Parquet analysis failed: {e}")
            return f"DuckDB Parquet Query Error: {e}"

if __name__ == "__main__":
    print("Testing MultiKeyGeminiPool with 2026 Google Gen AI SDK...")
    pool = MultiKeyGeminiPool(api_keys=[])
    key = pool._get_next_available_key()
    print(f"Key pool initialized successfully! Selected key: {key[:8]}...")
