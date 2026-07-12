import hmac
import hashlib

MASTER_SECRET = "SPINEDGE_MASTER_SECRET_2025_V1"

def generate_key(payload):
    # Replicate logic from core/licensing.py
    message = payload.encode('utf-8')
    secret = MASTER_SECRET.encode('utf-8')
    
    signature = hmac.new(secret, message, hashlib.sha256).hexdigest()
    sig_short = signature[:8].upper()
    
    return f"SPIN-{payload}-{sig_short}"

if __name__ == "__main__":
    print(f"Dev Key (Gold): {generate_key('GOLD_DEV')}")
    print(f"Dev Key (Test): {generate_key('TEST_USER')}")
