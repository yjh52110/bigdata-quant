import urllib.request
import json
import hashlib
from eth_account import Account

Account.enable_unaudited_hdwallet_features()

# Download BIP39 english wordlist
url = 'https://raw.githubusercontent.com/bitcoin/bips/master/bip-0039/english.txt'
print("正在获取 BIP-39 官方标准词表...")
with urllib.request.urlopen(url) as response:
    wordlist = response.read().decode('utf-8').splitlines()

word_to_index = {w: i for i, w in enumerate(wordlist)}

def check_mnemonic(words):
    if len(words) != 12: return False
    for w in words:
        if w not in word_to_index: return False
    indices = [word_to_index[w] for w in words]
    bit_str = ''.join(f'{i:011b}' for i in indices)
    ent_bytes = bytes(int(bit_str[:128][i:i+8], 2) for i in range(0, 128, 8))
    hash_bytes = hashlib.sha256(ent_bytes).digest()
    return bit_str[128:] == f'{(hash_bytes[0] >> 4):04b}'

base_words = ['wood', None, 'effort', 'twist', 'stand', 'creek', 'length', 'twice', 'hazard', 'faith', 'deputy', 'warm']

candidates = []
for w in wordlist:
    cand = list(base_words)
    cand[1] = w
    if check_mnemonic(cand):
        mnemonic_str = ' '.join(cand)
        addr = Account.from_mnemonic(mnemonic_str).address
        candidates.append((w, mnemonic_str, addr))

print(f"筛选出符合校验和规则的有效候选组合共 {len(candidates)} 个。")
print("开始在区块链网络上扫描资产与交易记录...\n")

def rpc_call(rpc_url, method, params):
    payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode('utf-8')
    req = urllib.request.Request(rpc_url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get('result')
    except Exception as e:
        return None

networks = [
    ("Ethereum 主网", "https://eth.llamarpc.com", "ETH"),
    ("BSC 币安智能链", "https://bsc-dataseed.binance.org", "BNB"),
    ("Polygon 网络", "https://polygon-rpc.com", "MATIC"),
    ("Arbitrum 网络", "https://arb1.arbitrum.io/rpc", "ETH"),
]

active_found = []

for net_name, rpc_url, symbol in networks:
    print(f"正在扫描 [{net_name}]...")
    for w, mnemonic_str, addr in candidates:
        bal_hex = rpc_call(rpc_url, 'eth_getBalance', [addr, 'latest'])
        tx_hex = rpc_call(rpc_url, 'eth_getTransactionCount', [addr, 'latest'])
        bal = int(bal_hex, 16) if bal_hex else 0
        tx = int(tx_hex, 16) if tx_hex else 0
        if bal > 0 or tx > 0:
            human_bal = bal / 1e18
            print("=" * 60)
            print(f"🎯 发现链上有活动记录！")
            print(f"网络: {net_name}")
            print(f"替换词 (第2个): {w}")
            print(f"助记词: {mnemonic_str}")
            print(f"钱包地址: {addr}")
            print(f"原生代币余额: {human_bal} {symbol}")
            print(f"交易笔数: {tx}")
            print("=" * 60)
            active_found.append((net_name, w, mnemonic_str, addr, human_bal, symbol, tx))

print("\n扫描完成！")
if not active_found:
    print("在主网常用链上暂未检索到有原生代币余额或历史交易的地址。")
