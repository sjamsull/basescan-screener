# 🎯 Token Screener - Project Structure

## 📁 Directory Tree

```
token-screener/
├── .github/
│   └── workflows/
│       └── scan-schedule.yml
├── collector/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── requirements.txt
│   ├── scanners/
│   │   ├── gmgn.py
│   │   ├── goplus.py
│   │   └── etherscan.py
│   ├── processors/
│   │   ├── screener.py
│   │   ├── deepdive.py
│   │   ├── risk.py
│   │   ├── scoring.py
│   │   └── security.py
│   ├── storage/
│   │   ├── local.py
│   │   └── supabase.py
│   └── utils/
│       ├── api.py
│       ├── helpers.py
│       └── validators.py
├── api/
│   ├── main.py
│   └── models.py
├── dashboard/
│   ├── app/
│   │   ├── page.tsx
│   │   └── token/
│   │       └── [addr]/
│   │           └── page.tsx
│   └── components/
│       ├── TokenCard.tsx
│       ├── WinrateChart.tsx
│       └── FilterPanel.tsx
├── shared/
│   ├── constants.py
│   └── types.py
├── scripts/
│   ├── run_scan.sh
│   └── migrate.sh
├── .env.example
├── .gitignore
└── README.md
```

---

## 🔧 Key File Contents

### 1️⃣ collector/config.py

```python
from dataclasses import dataclass
from typing import Dict
import os

@dataclass
class ChainConfig:
    name: str
    gmgn_id: str
    goplus_id: int
    explorer_chainid: int

CHAINS: Dict[str, ChainConfig] = {
    "base": ChainConfig("Base", "base", 8453, 8453),
    "robinhood": ChainConfig("Robinhood", "robinhood", 4663, None),
    "eth": ChainConfig("Ethereum", "eth", 1, 1),
    "bsc": ChainConfig("BSC", "bsc", 56, 56),
    "sol": ChainConfig("Solana", "sol", 101, None)
}

# Thresholds
MIN_AGE_DAYS = int(os.getenv("MIN_AGE_DAYS", "4"))
MAX_TOP10_PCT = float(os.getenv("MAX_TOP10_PCT", "65.0"))
MIN_LIQUIDITY_USD = int(os.getenv("MIN_LIQUIDITY_USD", "5000"))
MAX_TAX_PCT = float(os.getenv("MAX_TAX_PCT", "10.0"))
MIN_FEE_MC_RATIO = float(os.getenv("MIN_FEE_MC_RATIO", "1e-5"))

# Dead Whale Mode
DEAD_MAX_TOP10_PCT = 95.0
DEAD_MIN_LIQUIDITY_USD = 1000
DEAD_MIN_AGE_DAYS = 4
```

### 2️⃣ collector/scanners/gmgn.py

```python
import requests
import os
from typing import List, Dict, Optional

class GMGNClient:
    def __init__(self, chain: str = "base"):
        self.api_key = os.getenv("GMGN_API_KEY")
        self.chain = chain
        self.base_url = "https://gmgn.ai/defi/router/v1"
        
    def _headers(self) -> dict:
        return {"X-APIKEY": self.api_key} if self.api_key else {}
    
    def get_trending(self, mode: str = "accumulation", limit: int = 50) -> List[Dict]:
        orderby = "smart_degen_count" if mode == "accumulation" else "swaps"
        direction = "desc" if mode == "accumulation" else "asc"
        url = f"{self.base_url}/trending/{self.chain}"
        params = {"orderby": orderby, "direction": direction, "limit": limit}
        
        try:
            resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("list", [])
        except Exception as e:
            print(f"GMGN API Error: {e}")
            return []
    
    def get_token_info(self, address: str) -> Optional[Dict]:
        url = f"{self.base_url}/token_info"
        params = {"chain": self.chain, "address": address, "isCache": "false"}
        
        try:
            resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
            resp.raise_for_status()
            return resp.json().get("data")
        except Exception as e:
            print(f"Token info error for {address}: {e}")
            return None
```

### 3️⃣ collector/scanners/goplus.py

```python
import requests
import os
from typing import Dict

class GoPlusClient:
    def __init__(self):
        self.base_url = "https://api.gopluslabs.io/api/v1/token_security"
    
    def check_token(self, address: str, chain_id: int) -> Dict:
        url = f"{self.base_url}/{chain_id}"
        params = {"contract_addresses": address}
        
        try:
            resp = requests.get(url, params=params, timeout=30)
            data = resp.json()
            result = data.get("result", {}).get(address.lower(), {})
            
            return {
                "is_honeypot": result.get("is_honeypot", "0") == "1",
                "owner_renounced": result.get("owner_address") == "0x0000000000000000000000000000000000000000",
                "can_mint": result.get("is_mintable", "0") == "1",
                "can_blacklist": result.get("is_blacklisted", "0") == "1",
                "buy_tax": float(result.get("buy_tax", "0") or 0),
                "sell_tax": float(result.get("sell_tax", "0") or 0),
                "is_open_source": result.get("is_open_source", "0") == "1",
                "holder_count": int(result.get("holder_count", "0") or 0),
                "raw": result
            }
        except Exception as e:
            print(f"GoPlus error: {e}")
            return {"is_honeypot": None, "owner_renounced": None, "error": str(e)}
```

### 4️⃣ collector/processors/scoring.py

```python
from typing import Dict, Any

class TokenScorer:
    def __init__(self):
        self.weights = {
            "cluster_whale": 2,
            "accumulation_phase": 2,
            "kline_trend": 1,
            "liquidity_health": 1,
            "social_mention": 1
        }
    
    def calculate_momentum(self, token: Dict) -> float:
        score = 50
        kline = token.get("kline_trend_pct", 0)
        vol_ratio = token.get("kline_vol_ratio", 1)
        
        if 5 <= kline <= 120:
            score += 20
        elif kline > 120:
            score -= 15
        
        if vol_ratio > 1.5:
            score += 8
        
        if kline < -20:
            score -= 10
        
        return max(0, min(100, score))
    
    def calculate_alpha(self, token: Dict) -> float:
        score = 30
        total_weight = 0
        max_possible = 0
        
        if token.get("big_holder_count", 0) > 0:
            score += 20 * self.weights["cluster_whale"]
        total_weight += 1 * self.weights["cluster_whale"]
        max_possible += 20 * self.weights["cluster_whale"]
        
        if token.get("holder_count_trend") == "accumulation":
            score += 20 * self.weights["accumulation_phase"]
        total_weight += 1 * self.weights["accumulation_phase"]
        max_possible += 20 * self.weights["accumulation_phase"]
        
        momentum = self.calculate_momentum(token)
        score += (momentum - 50) * 0.4 * self.weights["kline_trend"]
        total_weight += 1 * self.weights["kline_trend"]
        max_possible += 20 * self.weights["kline_trend"]
        
        return max(0, min(100, score))
    
    def calculate_risk(self, token: Dict, security: Dict) -> float:
        risk = 0
        
        if security.get("is_honeypot"):
            return 100
        
        if not security.get("owner_renounced", True):
            risk += 10
        
        sell_tax = security.get("sell_tax", 0)
        if sell_tax > 10:
            risk += 25
        elif sell_tax > 5:
            risk += 10
        
        top10 = token.get("top_10_holder_rate", 0) * 100
        if top10 > 80:
            risk += 20
        elif top10 > 65:
            risk += 10
        
        liq = token.get("liquidity", 0)
        if liq < 50000:
            risk += 20
        elif liq < 100000:
            risk += 10
        
        return min(100, risk)
    
    def get_verdict(self, alpha: float, risk: float) -> str:
        composite = alpha - (risk * 0.5)
        
        if composite >= 80 and risk < 20:
            return "STRONG BUY"
        elif composite >= 60:
            return "BUY"
        elif composite >= 40:
            return "NEUTRAL"
        else:
            return "CAUTION"
```

### 5️⃣ collector/storage/supabase.py

```python
from supabase import create_client
import os
from typing import Dict, List
from datetime import datetime

class SupabaseStorage:
    def __init__(self):
        self.url = os.getenv("SUPABASE_URL")
        self.key = os.getenv("SUPABASE_KEY")
        
        if not self.url or not self.key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY required")
        
        self.client = create_client(self.url, self.key)
    
    def save_scan(self, tokens: List[Dict], chain: str, mode: str):
        scan_data = {
            "chain": chain,
            "mode": mode,
            "token_count": len(tokens),
            "scanned_at": datetime.utcnow().isoformat(),
            "tokens": tokens
        }
        return self.client.table("scans").insert(scan_data).execute()
    
    def get_latest(self, chain: str, limit: int = 1):
        return self.client.table("scans")\
            .select("*")\
            .eq("chain", chain)\
            .order("scanned_at", desc=True)\
            .limit(limit)\
            .execute()
```

### 6️⃣ collector/pipelines/full_scan.py

```python
from scanners.gmgn import GMGNClient
from scanners.goplus import GoPlusClient
from processors.scoring import TokenScorer
from storage.supabase import SupabaseStorage
import os

class TokenPipeline:
    def __init__(self, chain: str = "base"):
        self.chain = chain
        self.gmgn = GMGNClient(chain)
        self.goplus = GoPlusClient()
        self.scorer = TokenScorer()
        self.repo = SupabaseStorage()
    
    def run(self, mode: str = "accumulation", limit: int = 20):
        raw_tokens = self.gmgn.get_trending(mode=mode, limit=limit)
        
        results = []
        for token in raw_tokens:
            security = self.goplus.check_token(
                token["address"], 
                CHAINS[self.chain].goplus_id
            )
            
            alpha = self.scorer.calculate_alpha(token)
            risk = self.scorer.calculate_risk(token, security)
            verdict = self.scorer.get_verdict(alpha, risk)
            
            enriched = {
                **token,
                "alpha_score": alpha,
                "risk_score": risk,
                "verdict": verdict,
                "security": security,
                "chain": self.chain
            }
            results.append(enriched)
        
        self.repo.save_scan(results, self.chain, mode)
        return results

if __name__ == "__main__":
    import sys
    chain = sys.argv[1] if len(sys.argv) > 1 else "base"
    pipeline = TokenPipeline(chain)
    pipeline.run()
```

### 7️⃣ .github/workflows/scan-schedule.yml

```yaml
name: Token Scan Schedule
on:
  schedule:
    - cron: '0 * * * *'  # Every hour
  workflow_dispatch:  # Manual trigger

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -r collector/requirements.txt
      
      - name: Run scan
        env:
          GMGN_API_KEY: ${{ secrets.GMGN_API_KEY }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
        run: |
          cd collector
          python main.py --chain base --limit 50
```

### 8️⃣ .env.example

```env
# GMGN API
GMGN_API_KEY=your_gmgn_api_key_here

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_anon_key

# Optional: Etherscan
ETHERSCAN_API_KEY=your_etherscan_key

# Optional: GoPlus (free, no key needed usually)
# GOPLUS_API_KEY=your_goplus_key

# Scan Settings
MIN_AGE_DAYS=4
MAX_TOP10_PCT=65.0
MIN_LIQUIDITY_USD=5000
```

### 9️⃣ collector/requirements.txt

```txt
requests>=2.31.0
python-dotenv>=1.0.0
supabase>=2.0.0
```

---

## 🔄 Data Flow

```
SCRAPERS (GMGN, GoPlus, Etherscan)
    ↓
PROCESSORS (Screener → Deepdive → Risk → Scoring)
    ↓
STORAGE (Supabase PostgreSQL)
    ↓
API Layer (FastAPI / Supabase Edge Functions)
    ↓
DASHBOARD (Next.js on Netlify)
```

---

## 🚀 Deployment Stack

| Component | Platform | Cost |
|-----------|----------|------|
| Collector (Python) | GitHub Actions (cron) | FREE |
| Database | Supabase | FREE (500MB) |
| Dashboard | Netlify | FREE (100GB/mo) |

---

## ✅ Next Steps

1. Create repo GitHub
2. Copy structure ini
3. Setup Supabase project + tables
4. Add secrets ke GitHub (GMGN_API_KEY, SUPABASE_URL, SUPABASE_KEY)
5. Push ke GitHub → auto deploy dashboard + schedule scan jalan
