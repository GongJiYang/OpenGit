"""
Email Service - Email verification and notification

Provides email sending functionality for agent ownership verification.
Supports Resend API for production and console logging for development.
"""

import os
import logging

import aiohttp

logger = logging.getLogger(__name__)

# Configuration
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@agenthub.dev")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")


async def send_verification_email(
    to_email: str,
    agent_name: str,
    verify_url: str,
) -> bool:
    """
    Send verification email for agent ownership claim.

    Args:
        to_email: Recipient email address
        agent_name: Name of the agent being claimed
        verify_url: Full verification URL (including token)

    Returns:
        bool: True if email sent successfully
    """
    full_verify_url = f"{BASE_URL.rstrip('/')}{verify_url}"

    # Development mode: log instead of sending
    if not RESEND_API_KEY:
        logger.info(f"""
[DEV MODE] Verification Email
-----------------------------
To: {to_email}
Agent: {agent_name}
Verify URL: {full_verify_url}
-----------------------------
""")
        # In development, still return success
        return True

    # Production: send via Resend API
    try:
        async with aiohttp.ClientSession() as session:
            response = await session.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": FROM_EMAIL,
                    "to": [to_email],
                    "subject": f"Verify ownership of {agent_name} - AgentHub",
                    "html": _render_verification_email_html(
                        agent_name=agent_name,
                        verify_url=full_verify_url,
                    ),
                },
                timeout=aiohttp.ClientTimeout(total=10),
            )

            if response.status == 200:
                logger.info(f"Verification email sent to {to_email} for agent {agent_name}")
                return True
            else:
                error_text = await response.text()
                logger.error(f"Failed to send email: {response.status} - {error_text}")
                return False

    except aiohttp.ClientError as e:
        logger.error(f"Email send error: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending email: {e}")
        return False


def _render_verification_email_html(agent_name: str, verify_url: str) -> str:
    """Render the verification email HTML content."""
    return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
             background-color: #f5f5f5; margin: 0; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background: white;
                border-radius: 12px; padding: 40px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">

        <h1 style="color: #333; font-size: 24px; margin-bottom: 24px;">
            Confirm Agent Ownership
        </h1>

        <p style="color: #666; font-size: 16px; line-height: 1.6;">
            You are about to claim ownership of the agent:
        </p>

        <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 24px 0;">
            <strong style="font-size: 18px; color: #667eea;">{agent_name}</strong>
        </div>

        <p style="color: #666; font-size: 16px; line-height: 1.6;">
            Click the button below to verify your email and complete the claim:
        </p>

        <a href="{verify_url}"
           style="display: inline-block;
                  padding: 14px 32px;
                  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                  color: white;
                  text-decoration: none;
                  border-radius: 8px;
                  font-size: 16px;
                  font-weight: 600;
                  margin: 24px 0;">
            Verify Email Address
        </a>

        <p style="color: #999; font-size: 14px; margin-top: 32px;">
            This link will expire in <strong>30 minutes</strong>.
            If you did not request this verification, you can safely ignore this email.
        </p>

        <hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">

        <p style="color: #999; font-size: 12px;">
            AgentHub - Secure Agent Ownership Management
        </p>
    </div>
</body>
</html>
    """
