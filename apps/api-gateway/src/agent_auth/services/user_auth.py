"""
User Authentication Service

Handles:
- JWT token generation and validation
- Password hashing and verification
- OAuth profile management
- User-Agent permanent binding
"""

import os
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

from fastapi import Request
from sqlmodel import Session, select
from passlib.context import CryptContext
from jose import JWTError, jwt

from ..models.platform import (
    User,
    UserRole,
    UserAgentBinding,
    AuthProvider,
    UserCreate,
    UserLogin,
    UserResponse,
    TokenResponse,
)
from ..models import Agent, AgentStatus


# Configuration
JWT_SECRET = os.getenv("JWT_SECRET", "change-this-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = int(os.getenv("JWT_EXPIRATION_HOURS", "24"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class UserAuthService:
    """Service for human user authentication."""

    def __init__(self, session: Session):
        self.session = session

    # ============== Password Utilities ==============

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt (with passlib).

        Note: bcrypt has a 72-byte limit, so we truncate longer passwords.
        This is safe because truncating still maintains high entropy.
        """
        # bcrypt 限制：密码不能超过 72 字节
        password_bytes = password.encode('utf-8')
        if len(password_bytes) > 72:
            password = password_bytes[:72].decode('utf-8', errors='ignore')
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash.

        Applies the same truncation logic as hash_password.
        """
        # bcrypt 限制：密码不能超过 72 字节
        password_bytes = plain_password.encode('utf-8')
        if len(password_bytes) > 72:
            plain_password = password_bytes[:72].decode('utf-8', errors='ignore')
        return pwd_context.verify(plain_password, hashed_password)

    # ============== JWT Utilities ==============

    @staticmethod
    def create_access_token(user_id: str, expires_delta: timedelta = None) -> str:
        """Create a JWT access token for a user."""
        if expires_delta is None:
            expires_delta = timedelta(hours=JWT_EXPIRATION_HOURS)

        expire = datetime.utcnow() + expires_delta
        payload = {
            "sub": str(user_id),
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "access",
        }
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    @staticmethod
    def verify_token(token: str) -> Optional[dict]:
        """Verify and decode a JWT token."""
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            return payload
        except JWTError:
            return None

    # ============== User CRUD ==============

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        return self.session.get(User, user_id)

    def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        statement = select(User).where(User.email == email.lower())
        return self.session.exec(statement).first()

    def get_user_by_github_id(self, github_id: str) -> Optional[User]:
        """Get user by GitHub ID."""
        statement = select(User).where(User.github_id == github_id)
        return self.session.exec(statement).first()

    def create_user(self, email: str, password: Optional[str] = None,
                    display_name: Optional[str] = None) -> User:
        """Create a new user with email."""
        user = User(
            email=email.lower(),
            email_verified=False,
            display_name=display_name or email.split("@")[0],
            password_hash=self.hash_password(password) if password else None,
        )
        self.session.add(user)
        self.session.commit()
        self.session.refresh(user)
        return user

    def create_or_update_oauth_user(
        self,
        provider: AuthProvider,
        provider_id: str,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        **extra_fields
    ) -> User:
        """Create or update user from OAuth login."""

        if provider == AuthProvider.GITHUB:
            user = self.get_user_by_github_id(provider_id)
            if user:
                # Update existing user
                user.github_login = extra_fields.get("login", user.github_login)
                user.github_avatar = avatar_url or user.github_avatar
                user.display_name = display_name or user.display_name
                user.last_login_at = datetime.utcnow()
                if email and not user.email:
                    user.email = email
                self.session.add(user)
                self.session.commit()
                self.session.refresh(user)
                return user

            # Create new user
            user = User(
                email=email,
                github_id=provider_id,
                github_login=extra_fields.get("login"),
                github_avatar=avatar_url,
                display_name=display_name,
                avatar_url=avatar_url,
                last_login_at=datetime.utcnow(),
            )
            self.session.add(user)
            self.session.commit()
            self.session.refresh(user)
            return user

        raise ValueError(f"Unsupported OAuth provider: {provider}")

    # ============== Authentication ==============

    def authenticate_email(self, email: str, password: str) -> Tuple[Optional[User], Optional[str]]:
        """
        Authenticate user with email and password.

        Returns:
            Tuple of (user, error_message)
        """
        user = self.get_user_by_email(email)
        if not user:
            return None, "Invalid email or password"

        if not user.password_hash:
            return None, "Please use OAuth to login"

        if not self.verify_password(password, user.password_hash):
            return None, "Invalid email or password"

        # Update last login
        user.last_login_at = datetime.utcnow()
        self.session.add(user)
        self.session.commit()

        return user, None

    def login(self, email: str, password: str) -> Tuple[Optional[TokenResponse], Optional[str]]:
        """
        Login user and return JWT token.

        Returns:
            Tuple of (TokenResponse, error_message)
        """
        user, error = self.authenticate_email(email, password)
        if error:
            return None, error

        token = self.create_access_token(str(user.id))
        return TokenResponse(
            access_token=token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                display_name=user.display_name,
                github_login=user.github_login,
                avatar_url=user.avatar_url,
                role=user.role,
                created_at=user.created_at,
            )
        ), None

    def register(self, data: UserCreate) -> Tuple[Optional[TokenResponse], Optional[str]]:
        """
        Register a new user.

        Returns:
            Tuple of (TokenResponse, error_message)
        """
        # Check if email exists
        existing = self.get_user_by_email(data.email)
        if existing:
            return None, "Email already registered"

        # Create user
        user = self.create_user(
            email=data.email,
            password=data.password,
        )

        # Generate token
        token = self.create_access_token(str(user.id))
        return TokenResponse(
            access_token=token,
            user=UserResponse(
                id=user.id,
                email=user.email,
                display_name=user.display_name,
                github_login=user.github_login,
                avatar_url=user.avatar_url,
                role=user.role,
                created_at=user.created_at,
            )
        ), None

    # ============== User-Agent Binding ==============

    def bind_agent_to_user(self, user: User, agent: Agent, ip_address: str = None) -> UserAgentBinding:
        """
        Permanently bind an agent to a user.

        Rules:
        - One user can only have ONE bound agent
        - One agent can only be bound to ONE user
        - Binding is permanent
        """
        # Check if user already has a bound agent
        existing = self.session.exec(
            select(UserAgentBinding).where(UserAgentBinding.user_id == user.id)
        ).first()
        if existing:
            raise ValueError("User already has a bound agent")

        # Check if agent is already bound
        existing = self.session.exec(
            select(UserAgentBinding).where(UserAgentBinding.agent_id == agent.id)
        ).first()
        if existing:
            raise ValueError("Agent is already bound to another user")

        # Create binding
        binding = UserAgentBinding(
            user_id=user.id,
            agent_id=agent.id,
            ip_address=ip_address,
            is_permanent=True,
        )
        self.session.add(binding)

        # Update agent owner info
        agent.owner_email = user.email
        agent.owner_github_id = user.github_id
        agent.owner_github_login = user.github_login
        agent.status = AgentStatus.CLAIMED
        agent.claimed_at = datetime.utcnow()
        self.session.add(agent)

        self.session.commit()
        self.session.refresh(binding)
        return binding

    def get_user_bound_agent(self, user: User) -> Optional[Agent]:
        """Get the agent bound to a user."""
        binding = self.session.exec(
            select(UserAgentBinding).where(UserAgentBinding.user_id == user.id)
        ).first()
        if not binding:
            return None
        return self.session.get(Agent, binding.agent_id)

    def get_agent_bound_user(self, agent: Agent) -> Optional[User]:
        """Get the user who bound an agent."""
        binding = self.session.exec(
            select(UserAgentBinding).where(UserAgentBinding.agent_id == agent.id)
        ).first()
        if not binding:
            return None
        return self.session.get(User, binding.user_id)


# ============== FastAPI Depends ==============

from fastapi import Depends, HTTPException, status, Header
from typing import Annotated


async def get_current_user(
    authorization: Annotated[str, Header()],
) -> User:
    """
    Validate JWT token and return the authenticated user.

    Use this for human user endpoints.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header. Use: Bearer <token>"
        )

    token = authorization[7:]  # Remove "Bearer "

    # Import here to avoid circular dependency
    from ..database import get_db
    session = next(get_db())

    try:
        service = UserAuthService(session)

        payload = service.verify_token(token)
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token"
            )

        user_id = payload.get("sub")
        user = service.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found"
            )

        return user
    finally:
        session.close()


async def get_current_user_optional(
    authorization: Annotated[Optional[str], Header()] = None,
) -> Optional[User]:
    """
    Validate JWT token and return the authenticated user if present.

    Returns None if no authorization header or invalid token.
    Use this for endpoints that work with or without authentication.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization[7:]  # Remove "Bearer "

    # Import here to avoid circular dependency
    from ..database import get_db
    session = next(get_db())

    try:
        service = UserAuthService(session)

        payload = service.verify_token(token)
        if not payload:
            return None

        user_id = payload.get("sub")
        user = service.get_user_by_id(user_id)
        return user
    finally:
        session.close()


# Import here to avoid circular dependency
from ..database import get_db
