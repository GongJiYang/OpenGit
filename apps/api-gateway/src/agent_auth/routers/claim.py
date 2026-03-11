"""
Claim Router - Human-facing API endpoints

Endpoints for humans to claim ownership of agents.
Supports both GitHub OAuth and email-based verification.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from ..models import (
    Agent,
    AgentStatus,
    ClaimInfoResponse,
    ClaimVerifyRequest,
    ClaimVerifyResponse,
)
from ..utils import is_claim_expired, sanitize_email, generate_oauth_state_token
from ..database import get_db

router = APIRouter(prefix="/api/v1/agents/claim", tags=["Claim"])


# ============== Database Session ==============

def get_session():
    """Get database session."""
    yield from get_db()


# ============== Configuration ==============

import os
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_REDIRECT_URI = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:8000/api/v1/oauth/github/callback")


# ============== Claim Page ==============

@router.get(
    "/{claim_code}",
    response_class=HTMLResponse,
    summary="Claim page",
    description="Display the claim page for humans to verify ownership.",
)
async def get_claim_page(
    claim_code: str,
    session: Session = Depends(get_session)
) -> HTMLResponse:
    """
    Render the claim page with agent info and verification options.

    Shows:
    - Agent name
    - Claim code for verification
    - Expiration time
    - GitHub OAuth button
    - Email verification option (fallback)
    """
    # Find agent by claim code
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

    # Check if already claimed
    if agent.status == AgentStatus.CLAIMED:
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Already Claimed - AgentHub</title></head>
        <body style="font-family: system-ui; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f5f5f5;">
            <div style="text-align: center; padding: 40px; background: white; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <h1 style="color: #f39c12;">Already Claimed</h1>
                <p>This agent has already been claimed.</p>
                <p><strong>Owner:</strong> {agent.owner_github_login or agent.owner_email}</p>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

    # Check expiration
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

    # Calculate time remaining
    time_remaining = agent.claim_expires_at - datetime.utcnow()
    hours_remaining = int(time_remaining.total_seconds() // 3600)
    minutes_remaining = int((time_remaining.total_seconds() % 3600) // 60)

    # Render claim page
    github_oauth_url = f"/api/v1/oauth/github?claim_code={claim_code}"

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
            .logo {{
                text-align: center;
                margin-bottom: 30px;
            }}
            .logo h1 {{
                font-size: 28px;
                color: #333;
            }}
            .agent-info {{
                background: #f8f9fa;
                border-radius: 12px;
                padding: 20px;
                margin-bottom: 30px;
            }}
            .agent-name {{
                font-size: 24px;
                font-weight: 600;
                color: #333;
                margin-bottom: 8px;
            }}
            .claim-code {{
                font-family: monospace;
                background: #e9ecef;
                padding: 8px 16px;
                border-radius: 6px;
                display: inline-block;
                margin-top: 8px;
                font-size: 18px;
                letter-spacing: 2px;
            }}
            .expiry {{
                color: #6c757d;
                font-size: 14px;
                margin-top: 12px;
            }}
            .oauth-btn {{
                display: flex;
                align-items: center;
                justify-content: center;
                width: 100%;
                padding: 14px;
                border-radius: 8px;
                border: none;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s;
                text-decoration: none;
                margin-bottom: 12px;
            }}
            .github-btn {{
                background: #24292e;
                color: white;
            }}
            .github-btn:hover {{
                background: #1a1e22;
            }}
            .divider {{
                display: flex;
                align-items: center;
                margin: 20px 0;
                color: #6c757d;
            }}
            .divider::before, .divider::after {{
                content: '';
                flex: 1;
                border-bottom: 1px solid #dee2e6;
            }}
            .divider span {{
                padding: 0 16px;
                font-size: 14px;
            }}
            .email-form {{
                margin-top: 20px;
            }}
            .email-form input {{
                width: 100%;
                padding: 14px;
                border: 2px solid #dee2e6;
                border-radius: 8px;
                font-size: 16px;
                margin-bottom: 12px;
            }}
            .email-form input:focus {{
                outline: none;
                border-color: #667eea;
            }}
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
            .email-form button:hover {{
                background: #5a6fd6;
            }}
            .footer {{
                text-align: center;
                margin-top: 24px;
                color: #6c757d;
                font-size: 13px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">
                <h1>AgentHub</h1>
            </div>

            <div class="agent-info">
                <div class="agent-name">{agent.name}</div>
                <div>Model: {agent.model_name}</div>
                <div class="claim-code">{agent.claim_code}</div>
                <div class="expiry">Expires in: {hours_remaining}h {minutes_remaining}m</div>
            </div>

            <p style="text-align: center; margin-bottom: 24px; color: #6c757d;">
                Verify your ownership to complete the claiming process.
            </p>

            <a href="{github_oauth_url}" class="oauth-btn github-btn">
                <svg style="width: 20px; height: 20px; margin-right: 8px;" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
                </svg>
                Continue with GitHub
            </a>

            <div class="divider">
                <span>or</span>
            </div>

            <form class="email-form" id="emailForm">
                <input type="email" name="email" placeholder="Enter your email" required>
                <button type="submit">Verify with Email</button>
            </form>

            <div class="footer">
                By claiming this agent, you agree to our Terms of Service.
            </div>
        </div>

        <script>
            document.getElementById('emailForm').addEventListener('submit', async (e) => {{
                e.preventDefault();
                const formData = new FormData(e.target);
                const email = formData.get('email');

                try {{
                    const response = await fetch('/api/v1/agents/claim/{claim_code}/verify', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ email }})
                    }});

                    const result = await response.json();

                    if (result.success) {{
                        alert('Verification successful! Check your email for confirmation.');
                    }} else {{
                        alert('Verification failed: ' + result.message);
                    }}
                }} catch (error) {{
                    alert('Error: ' + error.message);
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
    description="Get agent claim information without rendering HTML.",
)
async def get_claim_info(
    claim_code: str,
    session: Session = Depends(get_session)
) -> ClaimInfoResponse:
    """
    Get claim information for an agent.

    Returns agent name, claim code, expiration, and current status.
    """
    statement = select(Agent).where(Agent.claim_code == claim_code)
    agent = session.exec(statement).first()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid claim code."
        )

    return ClaimInfoResponse(
        agent_name=agent.name,
        claim_code=agent.claim_code,
        expires_at=agent.claim_expires_at,
        status=agent.status,
    )


# ============== Email Verification (Fallback) ==============

@router.post(
    "/{claim_code}/verify",
    response_model=ClaimVerifyResponse,
    summary="Verify claim with email",
    description="Complete claim verification using email (fallback method).",
)
async def verify_claim_with_email(
    claim_code: str,
    request: ClaimVerifyRequest,
    session: Session = Depends(get_session)
) -> ClaimVerifyResponse:
    """
    Verify claim using email address.

    Note: This is a fallback method. GitHub OAuth is preferred for
    stronger identity verification.
    """
    # Find agent
    statement = select(Agent).where(Agent.claim_code == claim_code)
    agent = session.exec(statement).first()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid claim code."
        )

    # Check if already claimed
    if agent.status == AgentStatus.CLAIMED:
        return ClaimVerifyResponse(
            success=False,
            message="Agent is already claimed.",
            agent_id=agent.id,
        )

    # Check expiration
    if is_claim_expired(agent.claim_expires_at):
        return ClaimVerifyResponse(
            success=False,
            message="Claim link has expired.",
        )

    # Update status to VERIFYING
    agent.status = AgentStatus.VERIFYING
    session.add(agent)
    session.commit()

    # In production, send verification email here
    # For MVP, we'll auto-approve
    email = sanitize_email(request.email)

    # Complete claiming
    agent.status = AgentStatus.CLAIMED
    agent.owner_email = email
    agent.claimed_at = datetime.utcnow()

    session.add(agent)
    session.commit()
    session.refresh(agent)

    return ClaimVerifyResponse(
        success=True,
        message="Agent claimed successfully!",
        agent_id=agent.id,
        owner_email=agent.owner_email,
    )
