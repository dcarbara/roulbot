
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.strategy_engine import calculate_win_amount, get_bet_type_and_numbers, PAYOUT_TABLE

def verify_logic():
    print("--- Verifying Payout Table Logic ---")
    print(f"Payout Table Config: {PAYOUT_TABLE}")
    
    # Test Cases
    scenarios = [
        {"bet_label": "red", "bet_amount": 10.0, "spin": 1, "desc": "Bet Red (10), Spin 1 (Red)"}, # Win 1:1
        {"bet_label": "red", "bet_amount": 10.0, "spin": 2, "desc": "Bet Red (10), Spin 2 (Black)"}, # Loss
        {"bet_label": "32", "bet_amount": 5.0, "spin": 32, "desc": "Bet Straight 32 (5), Spin 32"}, # Win 35:1
        {"bet_label": "1st12", "bet_amount": 10.0, "spin": 5, "desc": "Bet 1st12 (10), Spin 5 (1st Doz)"}, # Win 2:1
        {"bet_label": "odd", "bet_amount": 10.0, "spin": 2, "desc": "Bet Odd (10), Spin 2 (Even)"} # Loss
    ]
    
    for sc in scenarios:
        print(f"\nScenario: {sc['desc']}")
        # 1. Check mapping
        btype, covered = get_bet_type_and_numbers(sc['bet_label'])
        print(f"  Mapping: Label '{sc['bet_label']}' -> Type '{btype}' -> Covers {covered}")
        
        # 2. Check Win/Loss
        is_covered = sc['spin'] in covered
        print(f"  Win Check: Is {sc['spin']} in covered numbers? {is_covered}")
        
        # 3. Check Payout Calculation
        bets = [{'label': sc['bet_label'], 'amount': sc['bet_amount']}]
        total_win, details = calculate_win_amount(bets, sc['spin'])
        
        # Manually calc expected
        expected_payout_ratio = PAYOUT_TABLE.get(btype, 0)
        if is_covered:
            # Expected Total Return = Stake + (Stake * Ratio)
            expected_return = sc['bet_amount'] + (sc['bet_amount'] * expected_payout_ratio)
            print(f"  Payout Table Ratio: {expected_payout_ratio}:1")
        else:
            expected_return = 0.0
            
        print(f"  Expected Return: {expected_return}")
        print(f"  Calculated Return: {total_win}")
        
        if abs(total_win - expected_return) < 0.001:
            print("  ✅ Verification Passed")
        else:
            print("  ❌ Verification FAILED")

if __name__ == "__main__":
    verify_logic()
