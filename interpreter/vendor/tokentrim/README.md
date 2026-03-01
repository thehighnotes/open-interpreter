# Vendored tokentrim

This is a vendored copy of [tokentrim](https://github.com/KillianLucas/tokentrim) v0.1.13 with one bug fix.

## Why vendored?

The upstream package has a double-subtraction bug in `trim()` that silently loses ~400-600 tokens of usable context per call. The system message token count is subtracted twice instead of once, making the trimmer overly aggressive and discarding conversation history that would otherwise fit.

**Filed upstream:** https://github.com/KillianLucas/tokentrim/issues/11

## The fix

In `tokentrim.py`, inside the `if system_message:` block of `trim()`, the duplicate line was removed:

```diff
  max_tokens -= system_message_tokens
- max_tokens -= system_message_tokens
```

That's the only change from upstream v0.1.13.
