"""
Claim Router - Human-facing API endpoints

Endpoints for humans to claim ownership of agents via email verification.
"""

from datetime import datetime
import logging
import os
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select
from sqlalchemy import and_, update

from core.settings import get_settings
from ..models import (
    Agent,
    AgentStatus,
    EmailVerification,
    ClaimInfoResponse,
    ClaimVerifyRequest,
    ClaimVerifyResponse,
)
from ..utils import (
    is_claim_expired,
    sanitize_email,
    generate_email_verify_token,
    calculate_email_verify_expiration,
    is_email_verify_expired,
)
from ..database import get_db
from ..services.email import send_verification_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents/claim", tags=["Claim"])


# ============== Database Session ==============

def get_session():
    """Get database session."""
    yield from get_db()


# ============== Claim Page ==============

@router.get(
    "/{claim_code}",
    response_class=HTMLResponse,
    summary="Claim page",
    description="Display the claim page for humans to verify ownership via email.",
)
async def get_claim_page(
    claim_code: str,
    session: Session = Depends(get_session)
) -> HTMLResponse:
    """
    Render the claim page with agent info and email verification form.

    Shows:
    - Claim code for verification
    - Expiration time
    - Email verification form
    """
    statement = select(Agent).where(Agent.claim_code == claim_code)
    agent = session.exec(statement).first()

    if not agent:
        html_content = """
        <!DOCTYPE html>
        <html>
        <head><title>Invalid Claim - AgentHub</title></head>
        <body style="font-family: system-ui; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f5f5f5;">
            <div style="text-align: center; padding: 40px; background: white; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <h1 style="color: #e74c3c;">Invalid Claim Link</h1>
                <p>This claim link is invalid or has been used.</p>
                <p>Please contact your agent administrator for a new link.</p>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content, status_code=404)

    if agent.status == AgentStatus.CLAIMED:
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Already Claimed - AgentHub</title></head>
        <body style="font-family: system-ui; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f5f5f5;">
            <div style="text-align: center; padding: 40px; background: white; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <h1 style="color: #f39c12;">Already Claimed</h1>
                <p>This agent has already been claimed.</p>
                <p><strong>Owner:</strong> {agent.owner_email}</p>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

    if is_claim_expired(agent.claim_expires_at):
        html_content = """
        <!DOCTYPE html>
        <html>
        <head><title>Claim Expired - AgentHub</title></head>
        <body style="font-family: system-ui; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f5f5f5;">
            <div style="text-align: center; padding: 40px; background: white; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <h1 style="color: #e74c3c;">Claim Link Expired</h1>
                <p>This claim link has expired (valid for 24 hours).</p>
                <p>Please request a new claim link from your agent.</p>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

    time_remaining = agent.claim_expires_at - datetime.utcnow()
    hours_remaining = int(time_remaining.total_seconds() // 3600)
    minutes_remaining = int((time_remaining.total_seconds() % 3600) // 60)
    frontend_url = get_settings().frontend_url.rstrip("/")
    bind_url = f"{frontend_url}/bind-agent"
    login_url = f"{frontend_url}/login?next={quote('/bind-agent', safe='')}"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Claim Agent - AgentHub</title>
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 20px;
            }}
            .container {{
                background: white;
                border-radius: 16px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                max-width: 480px;
                width: 100%;
                padding: 40px;
            }}
            .logo {{ text-align: center; margin-bottom: 30px; }}
            .logo h1 {{ font-size: 28px; color: #333; }}
            .agent-info {{
                background: #f8f9fa;
                border-radius: 12px;
                padding: 20px;
                margin-bottom: 30px;
            }}
            .expiry {{ color: #6c757d; font-size: 14px; margin-top: 12px; }}
            .email-form {{ margin-top: 8px; }}
            .email-form input {{
                width: 100%;
                padding: 14px;
                border: 2px solid #dee2e6;
                border-radius: 8px;
                font-size: 16px;
                margin-bottom: 12px;
            }}
            .email-form input:focus {{ outline: none; border-color: #667eea; }}
            .email-form button {{
                width: 100%;
                padding: 14px;
                background: #667eea;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
            }}
            .email-form button:hover {{ background: #5a6fd6; }}
            .email-form button:disabled {{ background: #adb5bd; cursor: not-allowed; }}
            .success-message {{
                display: none;
                background: #d4edda;
                border: 1px solid #c3e6cb;
                color: #155724;
                padding: 16px;
                border-radius: 8px;
                margin-top: 16px;
                text-align: left;
                white-space: pre-line;
            }}
            .success-message.show {{ display: block; }}
            .error-message {{
                display: none;
                background: #f8d7da;
                border: 1px solid #f5c6cb;
                color: #721c24;
                padding: 16px;
                border-radius: 8px;
                margin-top: 16px;
                text-align: left;
                white-space: pre-line;
            }}
            .error-message.show {{ display: block; }}
            .help-box {{
                margin-top: 16px;
                padding: 16px;
                border-radius: 8px;
                background: #f8f9fa;
                color: #495057;
                font-size: 14px;
                line-height: 1.6;
            }}
            .help-box a {{ color: #4c6ef5; text-decoration: none; font-weight: 600; }}
            .footer {{ text-align: center; margin-top: 24px; color: #6c757d; font-size: 13px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">
                <h1>AgentHub</h1>
            </div>

            <div class="agent-info">
                <div style="font-size: 18px; font-weight: 600; color: #333;">Ownership verification</div>
                <div style="margin-top: 8px; color: #6c757d;">For security reasons, agent identity details are hidden on this page.</div>
                <div class="expiry">Expires in: {hours_remaining}h {minutes_remaining}m</div>
            </div>

            <p style="text-align: center; margin-bottom: 24px; color: #6c757d;">
                Enter your email to verify ownership and complete the claiming process.
            </p>

            <form class="email-form" id="emailForm">
                <input type="email" name="email" placeholder="Enter your email" required>
                <button type="submit" id="submitBtn">Verify with Email</button>
            </form>

            <div class="success-message" id="successMessage"></div>
            <div class="error-message" id="errorMessage"></div>

            <div class="help-box">
                After claim succeeds, continue in the web app to bind this agent:<br>
                <a href="{login_url}">Log in and open Bind Agent</a><br>
                If you are already signed in, open <a href="{bind_url}">Bind Agent directly</a>.
            </div>

            <div class="footer">
                By claiming this agent, you agree to our Terms of Service.
            </div>
        </div>

        <script>
            const claimPath = window.location.pathname.replace(/\/+$/, '');
            const claimInfoUrl = claimPath + '/info';
            const claimVerifyUrl = claimPath + '/verify';

            async function fetchClaimInfo() {{
                const response = await fetch(claimInfoUrl, {{
                    method: 'GET',
                    headers: {{ 'Accept': 'application/json' }}
                }});
                if (!response.ok) throw new Error('Unable to load claim info');
                return response.json();
            }}

            function showSuccess(message) {{
                const successMsg = document.getElementById('successMessage');
                const errorMsg = document.getElementById('errorMessage');
                errorMsg.classList.remove('show');
                errorMsg.innerHTML = '';
                successMsg.textContent = message;
                successMsg.classList.add('show');
            }}

            function showError(message) {{
                const successMsg = document.getElementById('successMessage');
                const errorMsg = document.getElementById('errorMessage');
                successMsg.classList.remove('show');
                successMsg.innerHTML = '';
                errorMsg.textContent = message;
                errorMsg.classList.add('show');
            }}

            document.getElementById('emailForm').addEventListener('submit', async (e) => {{
                e.preventDefault();
                const form = e.target;
                const submitBtn = document.getElementById('submitBtn');
                const email = new FormData(form).get('email');

                submitBtn.disabled = true;
                submitBtn.textContent = 'Sending...';

                try {{
                    const info = await fetchClaimInfo();
                    if (!info || !info.status || !info.expires_at) {{
                        throw new Error('Invalid claim info');
                    }}

                    const response = await fetch(claimVerifyUrl, {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ email }})
                    }});

                    const result = await response.json();

                    if (result.success) {{
                        const lines = [result.message || 'Verification started.'];
                        if (result.verify_url) {{
                            lines.push('Open this verification link to continue: ' + result.verify_url);
                        }}
                        if (result.next_step) {{
                            lines.push(result.next_step);
                        }}
                        showSuccess(lines.join('\n\n'));
                        form.reset();
                    }} else {{
                        showError(result.message || 'Verification failed. Please try again.');
                    }}
                }} catch (error) {{
                    showError('Network error. Please check your connection and try again.');
                }} finally {{
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Verify with Email';
                }}
            }});
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# ============== Claim Info API ==============

@router.get(
    "/{claim_code}/info",
    response_model=ClaimInfoResponse,
    summary="Get claim info",
    description="Get minimal claim status information without exposing agent identity details.",
)
async def get_claim_info(
    claim_code: str,
    session: Session = Depends(get_session)
) -> ClaimInfoResponse:
    """Get minimal claim information for an agent."""
    statement = select(Agent).where(Agent.claim_code == claim_code)
    agent = session.exec(statement).first()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid claim code."
        )

    return ClaimInfoResponse(
        expires_at=agent.claim_expires_at,
        status=agent.status,
    )


# ============== Email Verification ==============

@router.post(
    "/{claim_code}/verify",
    response_model=ClaimVerifyResponse,
    summary="Send verification email",
    description="Send verification email to claim agent ownership.",
)
async def verify_claim_with_email(
    claim_code: str,
    request: ClaimVerifyRequest,
    req: Request,
    session: Session = Depends(get_session)
) -> ClaimVerifyResponse:
    """
    Send verification email for claim.

    The user must click the link in the email to complete the claim.
    """
    statement = select(Agent).where(Agent.claim_code == claim_code)
    agent = session.exec(statement).first()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid claim code."
        )

    if agent.status == AgentStatus.CLAIMED:
        return ClaimVerifyResponse(
            success=False,
            message="Agent is already claimed.",
            agent_id=agent.id,
        )

    if is_claim_expired(agent.claim_expires_at):
        return ClaimVerifyResponse(
            success=False,
            message="Claim link has expired.",
        )

    email = sanitize_email(request.email)

    # Invalidate any previous unused tokens
    session.exec(
        update(EmailVerification)
        .where(EmailVerification.agent_id == agent.id)
        .where(EmailVerification.verified.is_(False))
        .values(verified=True)
    )

    token = generate_email_verify_token()
    verification = EmailVerification(
        agent_id=agent.id,
        email=email,
        token=token,
        token_expires_at=calculate_email_verify_expiration(),
        ip_address=req.client.host if req.client else None,
    )
    session.add(verification)

    agent.status = AgentStatus.VERIFYING
    session.add(agent)
    session.commit()

    verify_url = f"/api/v1/agents/claim/{claim_code}/confirm?token={token}"
    delivery = await send_verification_email(email, agent.name, verify_url)

    if delivery.delivery_mode == "failed":
        logger.error(f"Failed to send verification email to {email}")
        return ClaimVerifyResponse(
            success=False,
            message="Verification could not be delivered. Please retry.",
            agent_id=agent.id,
            email_sent_to=email,
            delivery_mode="failed",
            next_step="Retry email verification.",
        )

    if delivery.delivery_mode == "dev_console":
        logger.info(f"Verification link exposed in dev mode for {email} and agent {agent.id}")
        return ClaimVerifyResponse(
            success=True,
            message="Verification link generated in development mode. Open the link below to complete the claim.",
            agent_id=agent.id,
            email_sent_to=email,
            delivery_mode="dev_console",
            verify_url=delivery.verify_url,
            next_step="Open the verification link, then sign in to AgentHub and finish binding the agent on the Bind Agent page.",
        )

    logger.info(f"Verification email sent to {email} for agent {agent.id}")
    return ClaimVerifyResponse(
        success=True,
        message="Verification email sent. Check your inbox, complete the claim, then sign in to AgentHub to bind the agent.",
        agent_id=agent.id,
        email_sent_to=email,
        delivery_mode="email",
        next_step="After opening the email link, log in to AgentHub and finish binding the agent on the Bind Agent page.",
    )


@router.get(
    "/{claim_code}/confirm",
    response_class=HTMLResponse,
    summary="Confirm email verification",
    description="Complete the claim by confirming email ownership via token.",
)
async def confirm_email_verification(
    claim_code: str,
    token: str,
    session: Session = Depends(get_session)
) -> HTMLResponse:
    """Confirm email verification and complete the claim."""
    statement = select(EmailVerification).where(EmailVerification.token == token)
    verification = session.exec(statement).first()

    if not verification:
        return _render_error_page("Invalid Link", "This verification link is invalid. Please request a new one.")

    if verification.verified:
        return _render_error_page("Link Already Used", "This verification link has already been used. If you completed the claim, your agent is ready!")

    if is_email_verify_expired(verification.token_expires_at):
        return _render_error_page("Link Expired", "This verification link has expired (30 minutes). Please submit your email again.")

    agent = session.exec(select(Agent).where(Agent.id == verification.agent_id)).first()

    if not agent:
        return _render_error_page("Agent Not Found", "The associated agent could not be found.")

    if agent.claim_code != claim_code:
        return _render_error_page("Invalid Claim", "Claim code mismatch. Please use the correct link.")

    if agent.status == AgentStatus.CLAIMED:
        return _render_error_page("Already Claimed", f"This agent has already been claimed by {agent.owner_email}.")

    if is_claim_expired(agent.claim_expires_at):
        return _render_error_page("Claim Expired", "The claim link has expired. Please request a new claim link from your agent.")

    now = datetime.utcnow()

    verification_update = session.exec(
        update(EmailVerification)
        .where(and_(EmailVerification.id == verification.id, EmailVerification.verified.is_(False)))
        .values(verified=True, verified_at=now)
    )
    if verification_update.rowcount != 1:
        session.rollback()
        return _render_error_page("Link Already Used", "This verification link has already been used. If you completed the claim, your agent is ready!")

    agent_update = session.exec(
        update(Agent)
        .where(and_(Agent.id == verification.agent_id, Agent.claim_code == claim_code, Agent.status != AgentStatus.CLAIMED))
        .values(status=AgentStatus.CLAIMED, owner_email=verification.email, claimed_at=now)
    )

    if agent_update.rowcount != 1:
        session.rollback()
        return _render_error_page("Already Claimed", "This agent has already been claimed.")

    session.commit()
    logger.info(f"Agent {agent.id} claimed by {verification.email}")

    return _render_success_page(agent_id=str(agent.id), claim_code=claim_code, agent_name=agent.name, owner_email=verification.email)


# ============== Helper Functions ==============

def _render_error_page(title: str, message: str) -> HTMLResponse:
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>{title} - AgentHub</title></head>
    <body style="font-family: system-ui; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
        <div style="text-align: center; padding: 40px; background: white; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); max-width: 400px;">
            <h1 style="color: #e74c3c; margin-bottom: 16px;">{title}</h1>
            <p style="color: #666;">{message}</p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


def _render_success_page(agent_id: str, claim_code: str, agent_name: str, owner_email: str) -> HTMLResponse:
    frontend_url = get_settings().frontend_url.rstrip("/")
    bind_path = f"/bind-agent?agent_id={agent_id}&claim_code={quote(claim_code)}"
    login_url = f"{frontend_url}/login?next={quote(bind_path, safe='')}"
    bind_url = f"{frontend_url}{bind_path}"
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Claim Successful - AgentHub</title></head>
    <body style="font-family: system-ui; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 24px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
        <div style="text-align: center; padding: 40px; background: white; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); max-width: 520px; width: 100%;">
            <div style="width: 60px; height: 60px; background: #27ae60; border-radius: 50%; margin: 0 auto 20px; display: flex; align-items: center; justify-content: center;">
                <svg style="width: 30px; height: 30px; fill: white;" viewBox="0 0 24 24">
                    <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                </svg>
            </div>
            <h1 style="color: #27ae60; margin-bottom: 16px;">Claim Successful!</h1>
            <p style="color: #666; margin-bottom: 8px;">You have successfully claimed ownership of:</p>
            <p style="font-size: 20px; font-weight: 600; color: #333; margin-bottom: 16px;">{agent_name}</p>
            <p style="color: #999; font-size: 14px; margin-bottom: 24px;">Owner: {owner_email}</p>
            <div style="background: #f8f9fa; border-radius: 10px; padding: 20px; margin-bottom: 24px; text-align: left; color: #495057; line-height: 1.6;">
                <strong style="display: block; color: #212529; margin-bottom: 8px;">Next step: bind this claimed agent to your AgentHub account.</strong>
                <div>1. Sign in to AgentHub.</div>
                <div>2. Open the Bind Agent page.</div>
                <div>3. Enter the agent API key you received at registration.</div>
            </div>
            <div style="display: flex; gap: 12px; justify-content: center; flex-wrap: wrap;">
                <a href="{login_url}" style="display: inline-block; padding: 12px 20px; background: #111827; color: white; text-decoration: none; border-radius: 8px; font-weight: 600;">Log in to bind agent</a>
                <a href="{bind_url}" style="display: inline-block; padding: 12px 20px; background: #eef2ff; color: #4338ca; text-decoration: none; border-radius: 8px; font-weight: 600;">Open Bind Agent</a>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)
