# code_agent/runtime/recovery.py

import random
import time
from dataclasses import dataclass


@dataclass
class RecoveryState:
    has_escalated: bool = False
    recovery_count: int = 0
    consecutive_529: int = 0
    has_attempted_reactive_compact: bool = False


class RecoveryPolicy:
    def __init__(
        self,
        max_retries: int = 3,
        base_delay_ms: int = 500,
        max_consecutive_529: int = 2,
    ):
        self.max_retries = max_retries
        self.base_delay_ms = base_delay_ms
        self.max_consecutive_529 = max_consecutive_529

    def retry_delay(self, attempt: int) -> float:
        base = min(self.base_delay_ms * (2 ** attempt), 32000) / 1000
        return base + random.uniform(0, base * 0.25)

    def with_retry(self, fn, state: RecoveryState):
        for attempt in range(self.max_retries):
            try:
                result = fn()
                state.consecutive_529 = 0
                return result

            except Exception as e:
                name = type(e).__name__.lower()
                msg = str(e).lower()

                if "ratelimit" in name or "rate_limit" in name or "429" in msg:
                    delay = self.retry_delay(attempt)
                    print(f"[429] retry {attempt + 1}/{self.max_retries} after {delay:.1f}s")
                    time.sleep(delay)
                    continue

                if "overloaded" in name or "529" in msg or "overloaded" in msg:
                    state.consecutive_529 += 1
                    delay = self.retry_delay(attempt)
                    print(f"[529] retry {attempt + 1}/{self.max_retries} after {delay:.1f}s")
                    time.sleep(delay)
                    continue

                raise

        raise RuntimeError(f"Max retries ({self.max_retries}) exceeded")

    def is_prompt_too_long_error(self, e: Exception) -> bool:
        msg = str(e).lower()
        return (
            ("prompt" in msg and "long" in msg)
            or "context_length_exceeded" in msg
            or "max_context_window" in msg
        )