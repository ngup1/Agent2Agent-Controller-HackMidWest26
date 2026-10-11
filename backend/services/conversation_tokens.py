"""Session tokens shared by the chat route and the trusted local replay CLI."""
import hashlib
import hmac


def conversation_token_for(secret, business_id, interaction_id):
    if len(secret) < 24:
        raise ValueError("Configure a long server-side CHAT_SESSION_SECRET")
    message = f"ssa014:{business_id}:{interaction_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
