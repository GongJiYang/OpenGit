from enum import Enum

class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"

class RepoRole(str, Enum):
    ARCHITECT = "architect"
    CONTRIBUTOR = "contributor"
    REVIEWER = "reviewer"
    EXECUTOR = "executor"
    BLACKBOX_TESTER = "tester"
    LIBRARIAN = "librarian"
    OBSERVER = "observer"

class MembershipStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING = "pending"
