"""
SpinEdge License Key Generator
Usage: python generate_key.py <TIER>
Tiers: BASIC, PLUS, PRO, ADMIN
"""
import hashlib
import hmac
import random
import string
import sys

MASTER_SECRET = "SPINEDGE_MASTER_SECRET_2025_V1"

def generate_key(tier="BASIC"):
    tier = tier.upper()
    valid_tiers = ["BASIC", "PLUS", "PRO", "ADMIN"]
    if tier not in valid_tiers:
        print(f"Invalid tier '{tier}'. Choose from: {', '.join(valid_tiers)}")
        return None

    # Generate random suffix (5 hex chars)
    random_suffix = ''.join(random.choices(string.hexdigits[:16], k=5)).upper()
    payload = f"{tier}_{random_suffix}"

    # HMAC-SHA256 signature (first 8 chars)
    message = payload.encode('utf-8')
    secret = MASTER_SECRET.encode('utf-8')
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()[:8].upper()

    key = f"SPIN-{payload}-{signature}"
    return key

if __name__ == "__main__":
    if len(sys.argv) > 1:
        tier = sys.argv[1].upper()
    else:
        tier = input("Enter tier (BASIC/PLUS/PRO/ADMIN): ").strip().upper()

    key = generate_key(tier)
    if key:
        print(f"\n{'='*40}")
        print(f"  Tier:  {tier}")
        print(f"  Key:   {key}")
        print(f"{'='*40}")
