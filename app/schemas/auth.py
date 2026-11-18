"""Authentication-related Pydantic schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    """Login request schema.

    ``tenant_slug`` scopes the login to a specific school when the
    same email is registered at more than one. Optional — when
    missing, the server returns the chooser payload (see
    MultipleTenantsException in auth_service).
    """

    email: EmailStr
    password: str = Field(..., min_length=8)
    remember_me: bool = False
    tenant_slug: str | None = Field(None, max_length=100, pattern=r"^[a-z0-9-]+$")


class LoginResponse(BaseModel):
    """Login response with tokens."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # Seconds until token expires


class LoginMfaRequest(BaseModel):
    """Second step of a 2FA-protected login.

    ``mfa_token`` is the short-lived challenge JWT returned by
    /auth/login when the user has 2FA enabled. ``code`` is either a
    6-digit TOTP token OR an 8-char backup code — backup codes are
    one-use and removed on success.
    """

    mfa_token: str
    code: str = Field(..., min_length=6, max_length=32)


class Setup2FAResponse(BaseModel):
    """Enrolment payload. Client renders the QR + offers a 'copy
    secret' affordance for users whose camera doesn't scan."""

    secret: str
    provisioning_uri: str
    qr_png: str  # base64 data-URL ready for <img src=>


class Verify2FARequest(BaseModel):
    """Confirm enrolment by submitting a token from the authenticator."""

    code: str = Field(..., min_length=6, max_length=6)


class Verify2FAResponse(BaseModel):
    """One-shot: the plaintext backup codes. Shown once, never
    retrievable — if the user loses them they must re-enrol."""

    backup_codes: list[str]


class Disable2FARequest(BaseModel):
    """Both current_password and a live TOTP code are required to
    turn 2FA off — a stolen password alone can't disable."""

    current_password: str = Field(..., min_length=8)
    code: str = Field(..., min_length=6, max_length=32)


def _phone_e164_optional(v: str | None) -> str | None:
    """Normalise an optional phone to E.164 or raise."""
    from app.utils.phone import PhoneValidationError, normalise_phone
    if v is None or not str(v).strip():
        return None
    try:
        return normalise_phone(v)
    except PhoneValidationError as e:
        raise ValueError(str(e))


def _phone_e164_required(v: str) -> str:
    """Normalise a required phone to E.164 or raise."""
    from app.utils.phone import PhoneValidationError, normalise_phone
    v = (v or "").strip() if v else ""
    if not v:
        raise ValueError("Mobile number is required")
    try:
        normalised = normalise_phone(v)
        if normalised is None:
            raise ValueError("Mobile number is required")
        return normalised
    except PhoneValidationError as e:
        raise ValueError(str(e))


class RegisterRequest(BaseModel):
    """Parent registration request (via invitation code).

    2026-10-09 onboarding redesign:
    - ``phone`` is now required (was optional). Enforced here so API
      callers get an immediate 422; the registration form also marks
      the field required.
    - ``whatsapp_opt_in`` / ``email_opt_in`` carry the parent's
      explicit channel choices. The admin's "Suggest WhatsApp opt-in"
      flag on the invitation only pre-ticks these on the form; the
      parent's final value is what lands here.
    """

    invitation_code: str = Field(..., min_length=8, max_length=8)
    email: EmailStr
    password: str = Field(..., min_length=8)
    confirm_password: str = Field(..., min_length=8)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: str = Field(..., max_length=50)
    whatsapp_opt_in: bool = False
    email_opt_in: bool = True

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        """Validate that passwords match."""
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("Passwords do not match")
        return v

    _phone_e164 = field_validator("phone")(lambda cls, v: _phone_e164_required(v))


class TeacherRegisterRequest(BaseModel):
    """Teacher registration via invitation code.

    Separate from the parent ``RegisterRequest`` because:
    - Teachers CAN leave phone blank (the UI labels it "(optional)")
      — parents can't. Before this split, any teacher who omitted
      phone got a cryptic "Registration failed" because Pydantic
      rejected ``phone=None`` on the required-str field.
    - Teachers don't have whatsapp_opt_in / email_opt_in choices at
      registration time — those live on the Profile page later.

    Shared validation: 8+ char password, confirm_password match,
    E.164 phone normalisation when provided.
    """

    invitation_code: str = Field(..., min_length=8, max_length=8)
    email: EmailStr
    password: str = Field(..., min_length=8)
    confirm_password: str = Field(..., min_length=8)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=50)

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("Passwords do not match")
        return v

    _phone_e164 = field_validator("phone")(
        lambda cls, v: _phone_e164_optional(v)
    )


class RegisterResponse(BaseModel):
    """Registration response."""

    user_id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    message: str = "Registration successful"


class RefreshTokenRequest(BaseModel):
    """Token refresh request."""

    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    """Forgot password request."""

    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    """Forgot password response."""

    message: str = "If your email is registered, you will receive password reset instructions"


class ResetPasswordRequest(BaseModel):
    """Password reset request."""

    token: str
    password: str = Field(..., min_length=8)
    confirm_password: str = Field(..., min_length=8)

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        """Validate that passwords match."""
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("Passwords do not match")
        return v


class ChangePasswordRequest(BaseModel):
    """Change password request (for authenticated users)."""

    current_password: str
    new_password: str = Field(..., min_length=8)
    confirm_password: str = Field(..., min_length=8)

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info) -> str:
        """Validate that passwords match."""
        if "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("Passwords do not match")
        return v


class UserProfile(BaseModel):
    """Current user profile schema."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID | None
    email: str
    first_name: str
    last_name: str
    phone: str | None
    role: str
    avatar_path: str | None
    language: str
    whatsapp_phone: str | None
    whatsapp_opted_in: bool
    email_opted_in: bool = True
    notification_prefs: dict = Field(default_factory=dict)
    totp_enabled: bool = False
    last_login_at: datetime | None
    created_at: datetime

    @property
    def full_name(self) -> str:
        """Get user's full name."""
        return f"{self.first_name} {self.last_name}"


class UpdateProfileRequest(BaseModel):
    """Update user profile request."""

    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    phone: str | None = Field(None, max_length=50)
    language: str | None = Field(None, max_length=5)
    whatsapp_phone: str | None = Field(None, max_length=50)
    whatsapp_opted_in: bool | None = None
    email_opted_in: bool | None = None
    # Shape: {event_slug: {"email": bool, "whatsapp": bool}}. Whole
    # dict replaces the stored one; omit a key to keep it. See
    # parent_notifier._event_channel_on for the read path.
    notification_prefs: dict | None = None

    _phone_e164 = field_validator("phone")(lambda cls, v: _phone_e164_optional(v))
    _wa_phone_e164 = field_validator("whatsapp_phone")(lambda cls, v: _phone_e164_optional(v))


class VerifyInvitationRequest(BaseModel):
    """Verify invitation code request."""

    code: str = Field(..., min_length=8, max_length=8)
    email: EmailStr


class VerifyInvitationResponse(BaseModel):
    """Verify invitation response."""

    valid: bool
    student_name: str | None = None
    school_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    message: str | None = None


class TrialSignupRequest(BaseModel):
    """Free trial signup request from the marketing site."""

    school_name: str = Field(..., min_length=1, max_length=200)
    contact_name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    phone: str = Field(..., min_length=1, max_length=50)
    country: str = Field("Zimbabwe", max_length=100)

    @field_validator("phone")
    @classmethod
    def _phone_e164(cls, v: str) -> str:
        from app.utils.phone import PhoneValidationError, normalise_phone
        v = (v or "").strip()
        if not v:
            raise ValueError("Phone is required for trial signup")
        try:
            n = normalise_phone(v)
            if n is None:
                raise ValueError("Phone is required for trial signup")
            return n
        except PhoneValidationError as e:
            raise ValueError(str(e))
    province: str | None = Field(None, max_length=100)
    school_type: str = Field(..., pattern=r"^(daycare|primary_school|high_school|combined|other)$")
    school_type_other: str | None = Field(None, max_length=200)
    student_count: int | None = Field(None, ge=1, le=10000)
    message: str | None = Field(None, max_length=1000)
