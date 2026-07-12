from typing import List

class DynamicNineStreetStrategy:
    def __init__(self, base_bet: float, k: int = 2):
        self.base_bet = base_bet
        self.k = k
        self.last_numbers: List[int] = []

    def reset(self):
        self.last_numbers = []

    def record_result(self, win: bool, last_number: int = None):
        if last_number is not None and 1 <= last_number <= 36:
            if win:
                # After a win, reset and start looking for new pattern
                print(f"[Dynamic9Street] WIN detected! Resetting pattern search.")
                self.last_numbers = [last_number]
            else:
                # On loss, continue building the pattern
                self.last_numbers.append(last_number)
                if len(self.last_numbers) > self.k:
                    self.last_numbers.pop(0)
        print(f"[Dynamic9Street] record_result: last_numbers={self.last_numbers}")

    def get_next_bet(self):
        return self.base_bet

    def get_current_bet(self):
        return self.base_bet

    def get_labels(self):
        # Only bet if we have k numbers
        if len(self.last_numbers) < self.k:
            print(f"[Dynamic9Street] get_labels: Not enough numbers yet (have {len(self.last_numbers)}, need {self.k})")
            return []
        # Map all last k numbers to their street index
        street_indices = [self.number_to_street_index(n) for n in self.last_numbers]
        print(f"[Dynamic9Street] get_labels: last_numbers={self.last_numbers}, street_indices={street_indices}")
        # If all share the same street index, bet on the other 3 in each dozen
        if len(set(street_indices)) == 1:
            hot_index = street_indices[0]
            labels = []
            for dozen in range(1, 4):
                for street in range(1, 5):
                    if street != hot_index:
                        labels.append(self.street_label(dozen, street))
            print(f"[Dynamic9Street] get_labels: Pattern detected! Betting on labels={labels}, skipping street index {hot_index}")
            return labels
        else:
            print(f"[Dynamic9Street] get_labels: No pattern detected (street indices not all the same)")
            return []

    @staticmethod
    def number_to_street_index(n: int):
        # n: 1-36
        # Returns street index (1-4) within its dozen
        if n < 1 or n > 36:
            return None
        return ((n - 1) % 12) // 3 + 1

    @staticmethod
    def number_to_dozen(n: int):
        if n < 1 or n > 36:
            return None
        return ((n - 1) // 12) + 1

    @staticmethod
    def street_label(dozen: int, street: int):
        # dozen: 1-3, street: 1-4
        start = (dozen - 1) * 12 + (street - 1) * 3 + 1
        end = start + 2
        return f"{start}-{end}strt" 